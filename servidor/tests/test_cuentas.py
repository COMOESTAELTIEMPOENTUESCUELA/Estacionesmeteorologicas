"""Cuentas de los pronosticadores: ingreso con Google, sesiones, permisos,
libreta y "Mis pronósticos". Contra una base real (sin TEST_DATABASE_URL se
saltean). Google se reemplaza por respuestas de prueba."""
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin TEST_DATABASE_URL")
X = {"X-Pedido": "libreta"}


@pytest.fixture
def cuentas(monkeypatch, cliente):
    from meteo.web import cuentas as m
    monkeypatch.setattr(m, "GOOGLE_CLIENT_ID", "id-prueba")
    monkeypatch.setattr(m, "GOOGLE_CLIENT_SECRET", "secreto")
    monkeypatch.setattr(m, "URL_PUBLICA", "https://prueba.ts.net")
    m.main(["agregar", "--email", "Ana@Gmail.com", "--nombre", "Ana", "--firma", "AnaP"])
    m.main(["agregar", "--email", "beto@gmail.com", "--nombre", "Beto"])
    m.main(["agregar", "--email", "coord@gmail.com", "--nombre", "Coord", "--rol", "coordinador"])
    yield m
    cliente.cookies.clear()
    with m._pools["e"].connection() as conn:
        conn.execute("DELETE FROM usuario WHERE email IN ('ana@gmail.com', 'beto@gmail.com', 'coord@gmail.com')")
        conn.commit()


def ingresar(cliente, cuentas, monkeypatch, email):
    """El flujo completo, con Google simulado: devuelve el cliente con la cookie."""
    r = cliente.get("/auth/google", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["https://prueba.ts.net/auth/google/volver"]

    class R:
        def __init__(self, datos):
            self.datos = datos
        def raise_for_status(self):
            pass
        def json(self):
            return self.datos

    pedidos = {}
    monkeypatch.setattr(cuentas.requests, "post", lambda url, data, timeout: pedidos.setdefault("post", data) and R({"access_token": "tok"}))
    monkeypatch.setattr(cuentas.requests, "get", lambda url, headers, timeout: R({"email": email, "email_verified": True}))
    r = cliente.get(f"/auth/google/volver?code=abc&state={q['state'][0]}", follow_redirects=False)
    if r.status_code == 302:
        assert pedidos["post"]["code_verifier"]          # PKCE: se manda el verificador
    return r


def test_sin_configurar_no_hay_ingreso(cliente):
    d = cliente.get("/api/yo").json()
    assert d["login"] is False and d["usuario"] is None
    assert cliente.get("/api/yo/notas").status_code == 401


def test_ingreso_solo_mails_permitidos(cliente, cuentas, monkeypatch):
    cliente.cookies.clear()
    r = ingresar(cliente, cuentas, monkeypatch, "intruso@gmail.com")
    assert r.status_code == 403 and "no está en la lista" in r.text
    r = ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    assert r.status_code == 302 and r.headers["location"] == "/#perfil"
    c = r.headers["set-cookie"]
    assert "meteo_sesion=" in c and "HttpOnly" in c and "Secure" in c and "samesite=lax" in c.lower()
    yo = cliente.get("/api/yo").json()["usuario"]
    assert yo["nombre"] == "Ana" and yo["firmas"] == ["AnaP"]
    # En la base, solo la huella del token.
    token = cliente.cookies.get("meteo_sesion")
    with cuentas._pools["l"].connection() as conn:
        assert not conn.execute("SELECT 1 FROM sesion WHERE huella = %s", (token,)).fetchall()
    assert cliente.post("/auth/salir").status_code == 204
    assert cliente.get("/api/yo").json()["usuario"] is None


def test_state_falso_no_entra(cliente, cuentas):
    cliente.cookies.clear()
    cliente.get("/auth/google", follow_redirects=False)
    assert cliente.get("/auth/google/volver?code=abc&state=otro", follow_redirects=False).status_code == 400


def test_libreta_privada_compartida_y_reunion(cliente, cuentas, monkeypatch):
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    nota = {"tipo": "pregunta", "texto": "¿Por qué el GFS profundiza tanto la baja?", "etiquetas": ["Ciclogénesis"],
            "compartida": True, "fecha_evento": "2026-10-28",
            "contexto": {"ts": "2026-10-28T00:00Z", "producto": "gfs_ana_mslp_jet", "<script>": "x", "ciudad": "La Plata"}}
    assert cliente.post("/api/yo/notas", json=nota).status_code == 403            # sin X-Pedido: rechazado
    n1 = cliente.post("/api/yo/notas", json=nota, headers=X).json()["id"]
    n2 = cliente.post("/api/yo/notas", json={"texto": "solo para mí", "tipo": "vi"}, headers=X).json()["id"]
    assert cliente.post("/api/yo/notas", json={"texto": "", "tipo": "vi"}, headers=X).status_code == 400
    mias = {n["id"]: n for n in cliente.get("/api/yo/notas").json()}
    assert mias[n1]["etiquetas"] == ["ciclogénesis"] and "<script>" not in mias[n1]["contexto"]
    # Beto ve la compartida pero no la privada, y no la puede editar ni borrar.
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
    vistas = {n["id"]: n for n in cliente.get("/api/yo/notas").json()}
    assert n1 in vistas and n2 not in vistas and vistas[n1]["autor"] == "Ana" and not vistas[n1]["mia"]
    assert cliente.put(f"/api/yo/notas/{n1}", json={"texto": "x"}, headers=X).status_code == 404
    assert cliente.delete(f"/api/yo/notas/{n1}", headers=X).status_code == 404
    assert cliente.put(f"/api/yo/notas/{n1}/reunion", json={"tratada": True}, headers=X).status_code == 404
    # Un coordinador la marca tratada en la reunión.
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "coord@gmail.com")
    assert [n["id"] for n in cliente.get("/api/yo/notas?tipo=pregunta&pendientes=true").json() if n["id"] == n1] == [n1]
    assert cliente.put(f"/api/yo/notas/{n1}/reunion", json={"tratada": True, "respuesta": "Falta de datos en el Pacífico"},
                       headers=X).status_code == 200
    n = [x for x in cliente.get("/api/yo/notas").json() if x["id"] == n1][0]
    assert n["tratada"] and n["tratada_por"] == "Coord" and n["respuesta"].startswith("Falta")


def test_mis_pronosticos_y_permisos(cliente, cuentas, monkeypatch):
    """Las métricas salen de la versión VIGENTE de cada pronóstico: si otro
    corrigió el de Ana, ese día no cuenta para Ana."""
    from meteo.web import cuentas as m
    with m._pools["e"].connection() as conn:
        for ciudad, por, cap, tmin in (("Ciudad Test", "AnaP", "2026-10-10 08:00Z", 10),
                                       ("Ciudad Test", "AnaP", "2026-10-11 08:00Z", 12),
                                       ("Ciudad Test", "Otro", "2026-10-11 10:00Z", 13)):   # corrige el del 11
            fecha = cap[:10]
            eid = conn.execute("""INSERT INTO pronostico_emision (ciudad, fecha_pronostico, publicado_por, contenido, huella, capturado)
                                  VALUES (%s, %s, %s, '{}', %s, %s) RETURNING id""",
                               (ciudad, fecha, por, f"test-{por}-{cap}", cap)).fetchone()["id"]
            conn.execute("""INSERT INTO pronostico_dia (emision_id, fecha, plazo_dias, temp_min, temp_max, periodos)
                            VALUES (%s, %s, 0, %s, 20, '[]')""", (eid, fecha, tmin))
            conn.execute("""INSERT INTO verif_observado (ciudad, fecha, elemento, valor, categoria, estacion_id, detalle)
                            VALUES (%s, %s, 'tmin_urbana', 9, 'madrugada', 'omm_87593', '{}') ON CONFLICT DO NOTHING""", (ciudad, fecha))
        conn.commit()
    try:
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
        d = cliente.get("/api/yo/pronosticos?desde=2026-10-01&hasta=2026-10-31").json()
        assert [(f["fecha"], f["tmin_urbana"]["pron"]) for f in d["dias"]] == [("2026-10-10", 10)]
        assert d["resumen"]["0"]["tmin_urbana"]["mae"] == 1.0
        beto = [u for u in m._leer("SELECT id FROM usuario WHERE email = 'beto@gmail.com'")][0]["id"]
        assert cliente.get(f"/api/yo/pronosticos?usuario_id={beto}").status_code == 403    # no es coordinador
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "coord@gmail.com")
        ana = m._leer("SELECT id FROM usuario WHERE email = 'ana@gmail.com'")[0]["id"]
        assert len(cliente.get(f"/api/yo/pronosticos?usuario_id={ana}&desde=2026-10-01&hasta=2026-10-31").json()["dias"]) == 1
        # Por firma, aunque no tenga usuario ("Otro"), y el resumen de todo el grupo.
        assert [f["tmin_urbana"]["pron"] for f in cliente.get(
            "/api/yo/pronosticos?firma=Otro&desde=2026-10-01&hasta=2026-10-31").json()["dias"]] == [13]
        g = {x["firma"]: x for x in cliente.get("/api/yo/pronosticos/grupo?desde=2026-10-01&hasta=2026-10-31").json()["pronosticadores"]}
        assert g["AnaP"]["usuario"]["nombre"] == "Ana" and g["AnaP"]["dias"] == 1 and g["Otro"]["usuario"] is None
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
        assert cliente.get("/api/yo/pronosticos/grupo").status_code == 403
        assert cliente.get("/api/yo/pronosticos?firma=AnaP").status_code == 403
    finally:
        with m._pools["e"].connection() as conn:
            conn.execute("DELETE FROM pronostico_emision WHERE ciudad = 'Ciudad Test'")
            conn.execute("DELETE FROM verif_observado WHERE ciudad = 'Ciudad Test'")
            conn.commit()


def test_copia_publica_solo_abre_las_cuentas(cliente, cuentas, monkeypatch):
    from meteo.web import app as web
    monkeypatch.setattr(web, "WEB_MODO", "cartas")
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    assert cliente.post("/api/yo/notas", json={"texto": "desde la copia pública", "tipo": "vi"}, headers=X).status_code == 200
    assert cliente.post("/api/dibujos", json={}).status_code in (404, 405)          # lo demás sigue cerrado
    assert cliente.get("/api/estaciones").status_code == 404


def test_usuario_desactivado_pierde_la_sesion(cliente, cuentas, monkeypatch):
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
    assert cliente.get("/api/yo").json()["usuario"]["nombre"] == "Beto"
    cuentas.main(["desactivar", "--email", "beto@gmail.com"])
    assert cliente.get("/api/yo").json()["usuario"] is None


def test_admin_ve_las_notas_privadas_de_todos(cliente, cuentas, monkeypatch):
    cuentas.main(["agregar", "--email", "admin@gmail.com", "--nombre", "Admin", "--rol", "admin"])
    try:
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
        privada = cliente.post("/api/yo/notas", json={"texto": "privada de Beto", "tipo": "duda"}, headers=X).json()["id"]
        assert cliente.get("/api/yo/notas?todas=true").status_code == 403          # Beto no es admin
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "coord@gmail.com")
        assert privada not in [n["id"] for n in cliente.get("/api/yo/notas").json()]
        assert cliente.get("/api/yo/notas?todas=true").status_code == 403          # coordinador tampoco
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "admin@gmail.com")
        assert privada not in [n["id"] for n in cliente.get("/api/yo/notas").json()]
        beto = cuentas._leer("SELECT id FROM usuario WHERE email = 'beto@gmail.com'")[0]["id"]
        vistas = cliente.get(f"/api/yo/notas?todas=true&autor_id={beto}").json()
        assert [(n["id"], n["autor"], n["compartida"]) for n in vistas] == [(privada, "Beto", False)]
    finally:
        with cuentas._pools["e"].connection() as conn:
            conn.execute("DELETE FROM usuario WHERE email = 'admin@gmail.com'")
            conn.commit()


def test_muro_comentarios_y_miniatura(cliente, cuentas, monkeypatch, tmp_path):
    """El muro del grupo: comentarios en las notas compartidas (no en las
    privadas de otros) y la imagen de la carta donde se escribió la nota."""
    from datetime import datetime, timezone

    from meteo.cartas import archivo
    monkeypatch.setattr(cuentas, "ARCHIVO_DIR", str(tmp_path))
    archivo.guardar(tmp_path, "gfs_ana_mslp_jet", datetime(2026, 10, 28, 0, tzinfo=timezone.utc), bytes([0x89]) + b"PNG\r\n\x1a\n" + b"0" * 50)
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    comp = cliente.post("/api/yo/notas", json={"texto": "frente frío entrando", "tipo": "vi", "compartida": True,
                                               "contexto": {"seccion": "analisis", "ts": "2026-10-28T00:00:00Z"}}, headers=X).json()["id"]
    priv = cliente.post("/api/yo/notas", json={"texto": "privada", "tipo": "vi"}, headers=X).json()["id"]
    n = [x for x in cliente.get("/api/yo/notas").json() if x["id"] == comp][0]
    assert n["imagen"] == "/cartas-img/gfs_ana_mslp_jet/2026/10/gfs_ana_mslp_jet_2026102800.png" and n["comentarios"] == []
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
    assert cliente.post(f"/api/yo/notas/{comp}/comentarios", json={"texto": "coincido, ver el chorro"}, headers=X).status_code == 200
    assert cliente.post(f"/api/yo/notas/{priv}/comentarios", json={"texto": "x"}, headers=X).status_code == 404
    assert cliente.post(f"/api/yo/notas/{comp}/comentarios", json={"texto": ""}, headers=X).status_code == 400
    c = [x for x in cliente.get("/api/yo/notas").json() if x["id"] == comp][0]["comentarios"]
    assert [(x["autor"], x["texto"], x["mio"]) for x in c] == [("Beto", "coincido, ver el chorro", True)]
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    assert cliente.delete(f"/api/yo/comentarios/{c[0]['id']}", headers=X).status_code == 404     # no es de Ana


def test_imagen_pegada_a_la_nota(cliente, cuentas, monkeypatch):
    """La "pizarra": la nota lleva una imagen (carta dibujada, meteograma
    marcado); se recodifica en WebP y solo la ve quien puede ver la nota."""
    import base64
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (3000, 600), (200, 30, 30)).save(buf, "PNG")
    dato = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    assert cliente.post("/api/yo/notas", json={"texto": "x", "imagen": "data:image/png;base64,AAAA"}, headers=X).status_code == 400
    assert cliente.post("/api/yo/notas", json={"texto": "x", "imagen": "data:text/html;base64,PGI+"}, headers=X).status_code == 400
    priv = cliente.post("/api/yo/notas", json={"texto": "mi pizarra", "imagen": dato}, headers=X).json()["id"]
    comp = cliente.post("/api/yo/notas", json={"texto": "para todos", "compartida": True, "imagen": dato}, headers=X).json()["id"]
    n = {x["id"]: x for x in cliente.get("/api/yo/notas").json()}
    assert n[priv]["captura"].startswith(f"/api/yo/notas/{priv}/imagen?v=")
    r = cliente.get(f"/api/yo/notas/{priv}/imagen")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(r.content)).size == (1600, 320)          # achicada
    # Editar sin "imagen" la deja; con null la saca.
    assert cliente.put(f"/api/yo/notas/{comp}", json={"texto": "para todos!", "compartida": True}, headers=X).status_code == 200
    assert cliente.get(f"/api/yo/notas/{comp}/imagen").status_code == 200
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
    assert cliente.get(f"/api/yo/notas/{priv}/imagen").status_code == 404       # privada de Ana
    assert cliente.get(f"/api/yo/notas/{comp}/imagen").status_code == 200
    cliente.cookies.clear()
    assert cliente.get(f"/api/yo/notas/{comp}/imagen").status_code == 401       # sin sesión
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    cliente.put(f"/api/yo/notas/{comp}", json={"texto": "sin imagen", "compartida": True, "imagen": None}, headers=X)
    assert cliente.get(f"/api/yo/notas/{comp}/imagen").status_code == 404


def test_red_muro_reacciones_y_metricas(cliente, cuentas, monkeypatch):
    """La parte "red social": el inicio mezcla notas y pronósticos con lo
    observado; reacciones y comentarios; métricas visibles solo si se elige."""
    m = cuentas
    with m._pools["e"].connection() as conn:
        for fecha, tmin, obs in (("2026-10-10", 10, 9), ("2026-10-11", 12, 12.5)):
            eid = conn.execute("""INSERT INTO pronostico_emision (ciudad, fecha_pronostico, publicado_por, contenido, huella, capturado)
                                  VALUES ('Ciudad Test', %s, 'AnaP', '{}', %s, %s) RETURNING id""",
                               (fecha, f"red-{fecha}", fecha + " 08:00Z")).fetchone()["id"]
            for k in (0, 1):
                conn.execute("""INSERT INTO pronostico_dia (emision_id, fecha, plazo_dias, temp_min, temp_max, periodos)
                                VALUES (%s, %s::date + %s, %s, %s, 20, '[]')""", (eid, fecha, k, k, tmin + k))
            conn.execute("""INSERT INTO verif_observado (ciudad, fecha, elemento, valor, categoria, estacion_id, detalle)
                            VALUES ('Ciudad Test', %s, 'tmin_urbana', %s, 'madrugada', 'omm_87593', '{}') ON CONFLICT DO NOTHING""", (fecha, obs))
        conn.commit()
    try:
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
        nota = cliente.post("/api/yo/notas", json={"texto": "se viene frío", "compartida": True}, headers=X).json()["id"]
        d = cliente.get("/api/yo/muro?hasta=2026-10-11&dias=2").json()
        pron = [i for i in d["items"] if i["tipo"] == "pronostico" and i["ciudad"] == "Ciudad Test"]
        assert [(p["fecha_emision"], p["usuario"]["nombre"]) for p in pron] == [("2026-10-11", "Ana"), ("2026-10-10", "Ana")]
        assert pron[1]["dias"][0]["tmin"]["pron"] == 10 and pron[1]["dias"][0]["tmin"]["obs"] == 9
        assert pron[0]["dias"][1]["tmin"]["obs"] is None                      # el día 2 todavía sin observar
        hoy = cliente.get("/api/yo/muro").json()
        assert [i["nota"]["id"] for i in hoy["items"] if i["tipo"] == "nota"] == [nota]
        # Reacciones (interruptor) y comentario en un pronóstico, de otro usuario.
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
        obj = pron[0]["objeto"]
        assert cliente.post("/api/yo/reacciones", json={"objeto": obj, "tipo": "aplauso"}, headers=X).json()["conteo"] == {"aplauso": 1}
        assert cliente.post("/api/yo/reacciones", json={"objeto": f"nota:{nota}", "tipo": "util"}, headers=X).json()["mias"] == ["util"]
        assert cliente.post("/api/yo/reacciones", json={"objeto": f"nota:{nota}", "tipo": "util"}, headers=X).json()["conteo"] == {}
        assert cliente.post("/api/yo/reacciones", json={"objeto": "pron|X|2026-01-01|Nadie", "tipo": "util"}, headers=X).status_code == 404
        assert cliente.post("/api/yo/reacciones", json={"objeto": obj, "tipo": "odio"}, headers=X).status_code == 400
        assert cliente.post("/api/yo/pronostico/comentarios", json={"objeto": obj, "texto": "¡buena!"}, headers=X).status_code == 200
        p0 = [i for i in cliente.get("/api/yo/muro?hasta=2026-10-11&dias=2").json()["items"] if i.get("objeto") == obj][0]
        assert p0["reacciones"]["quienes"]["aplauso"] == ["Beto"] and p0["comentarios"][0]["texto"] == "¡buena!"
        # Métricas de Ana: privadas hasta que ella elige mostrarlas.
        ana = m._leer("SELECT id FROM usuario WHERE email = 'ana@gmail.com'")[0]["id"]
        assert cliente.get(f"/api/yo/pronosticos?usuario_id={ana}").status_code == 403
        assert cliente.get(f"/api/yo/muro?autor_id={ana}&hasta=2026-10-11&dias=2").status_code == 200   # lo publicado sí se ve
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
        assert cliente.put("/api/yo/perfil", json={"bio": "Me gustan los frentes", "metricas_publicas": True}, headers=X).status_code == 200
        assert cliente.get("/api/yo").json()["usuario"]["bio"] == "Me gustan los frentes"
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
        assert cliente.get(f"/api/yo/pronosticos?usuario_id={ana}&desde=2026-10-01&hasta=2026-10-31").status_code == 200
        gente = {p["nombre"]: p for p in cliente.get("/api/yo/personas").json()["personas"] if p["id"]}
        assert gente["Ana"]["metricas_visibles"] and gente["Ana"]["bio"] == "Me gustan los frentes"
        assert not gente["Beto"]["metricas_visibles"] or gente["Beto"]["yo"]
    finally:
        with m._pools["e"].connection() as conn:
            conn.execute("DELETE FROM pronostico_emision WHERE ciudad = 'Ciudad Test'")
            conn.execute("DELETE FROM verif_observado WHERE ciudad = 'Ciudad Test'")
            conn.execute("DELETE FROM reaccion WHERE objeto LIKE 'pron|Ciudad Test|%'")
            conn.execute("DELETE FROM comentario_pronostico WHERE objeto LIKE 'pron|Ciudad Test|%'")
            conn.commit()


def test_hilos_e_insignias(cliente, cuentas, monkeypatch):
    m = cuentas
    with m._pools["e"].connection() as conn:
        eid = conn.execute("""INSERT INTO pronostico_emision (ciudad, fecha_pronostico, publicado_por, contenido, huella, capturado)
                              VALUES ('Ciudad Test', '2026-10-10', 'AnaP', '{}', 'ins-1', '2026-10-10 11:00Z') RETURNING id""").fetchone()["id"]
        for k, (tmin, tmax, omin, omax) in enumerate(((10, 20, 10.5, 19.4), (12, 25, 12.8, 25.0))):
            conn.execute("""INSERT INTO pronostico_dia (emision_id, fecha, plazo_dias, temp_min, temp_max, periodos)
                            VALUES (%s, %s::date + %s, %s, %s, %s, '[]')""", (eid, "2026-10-10", k, k, tmin, tmax))
            for el, v in (("tmin_urbana", omin), ("tmax_urbana", omax)):
                conn.execute("""INSERT INTO verif_observado (ciudad, fecha, elemento, valor, categoria, estacion_id, detalle)
                                VALUES ('Ciudad Test', %s::date + %s, %s, %s, NULL, 'omm_87593', '{}') ON CONFLICT DO NOTHING""", ("2026-10-10", k, el, v))
        conn.commit()
    try:
        # El GFS decía 5 °C menos de máxima el día 2: Ana se apartó y acertó.
        monkeypatch.setattr(m, "_gfs_diario", lambda ciudad, cap: ({date(2026, 10, 11): {"tmin": 12, "tmax": 20}}, "2026101006"))
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
        p = [i for i in cliente.get("/api/yo/muro?hasta=2026-10-10&dias=1").json()["items"] if i.get("ciudad") == "Ciudad Test"][0]
        assert set(p["insignias"]) == {"clavadas", "kamikaze"} and p["dias"][1]["gfs"]["tmax"] == 20
        nota = cliente.post("/api/yo/notas", json={"texto": "se ve la vaguada", "tipo": "vi", "compartida": True}, headers=X).json()["id"]
        otra = cliente.post("/api/yo/notas", json={"texto": "otra", "compartida": True}, headers=X).json()["id"]
        c1 = cliente.post(f"/api/yo/notas/{nota}/comentarios", json={"texto": "¿dónde?"}, headers=X).json()["id"]
        # Hilo: responder a un comentario; no se puede colgar de otra nota.
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")
        assert cliente.post(f"/api/yo/notas/{nota}/comentarios", json={"texto": "al oeste", "responde_a": c1}, headers=X).status_code == 200
        assert cliente.post(f"/api/yo/notas/{otra}/comentarios", json={"texto": "x", "responde_a": c1}, headers=X).status_code == 400
        cp = cliente.post("/api/yo/pronostico/comentarios", json={"objeto": p["objeto"], "texto": "¡bien!"}, headers=X).json()["id"]
        assert cliente.post("/api/yo/pronostico/comentarios", json={"objeto": p["objeto"], "texto": "sí", "responde_a": cp}, headers=X).status_code == 200
        cs = [n for n in cliente.get("/api/yo/notas").json() if n["id"] == nota][0]["comentarios"]
        assert [(c["texto"], c["responde_a"]) for c in cs] == [("¿dónde?", None), ("al oeste", c1)]
        # Ojo de halcón: la nota con "útil" de otros (2 en la prueba).
        monkeypatch.setattr(m, "MIN_UTIL_HALCON", 2)
        cliente.post("/api/yo/reacciones", json={"objeto": f"nota:{nota}", "tipo": "util"}, headers=X)
        cliente.cookies.clear()
        ingresar(cliente, cuentas, monkeypatch, "coord@gmail.com")
        cliente.post("/api/yo/reacciones", json={"objeto": f"nota:{nota}", "tipo": "util"}, headers=X)
        assert [n for n in cliente.get("/api/yo/notas").json() if n["id"] == nota][0]["insignias"] == ["halcon"]
    finally:
        with m._pools["e"].connection() as conn:
            conn.execute("DELETE FROM pronostico_emision WHERE ciudad = 'Ciudad Test'")
            conn.execute("DELETE FROM verif_observado WHERE ciudad = 'Ciudad Test'")
            conn.execute("DELETE FROM reaccion WHERE objeto LIKE 'pron|Ciudad Test|%'")
            conn.execute("DELETE FROM comentario_pronostico WHERE objeto LIKE 'pron|Ciudad Test|%'")
            conn.commit()


def test_capa_no_destructiva(cliente, cuentas, monkeypatch):
    """La pizarra guarda la imagen de base y el dibujo aparte (elementos)."""
    import base64
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (200, 150), (255, 255, 255)).save(buf, "PNG")
    dato = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    capa = {"ancho": 1000, "alto": 750, "elementos": [{"tipo": "simbolo", "simbolo": "tormenta", "x": 500, "y": 300, "tam": 40},
                                                       {"tipo": "trazo", "color": "#dc2626", "ancho": 5, "puntos": [[1, 2], [30, 40]]}]}
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    nid = cliente.post("/api/yo/notas", json={"texto": "con capa", "imagen": dato, "capa": capa}, headers=X).json()["id"]
    n = [x for x in cliente.get("/api/yo/notas").json() if x["id"] == nid][0]
    assert n["capa"]["elementos"][0]["simbolo"] == "tormenta" and n["captura"]
    malo = {**capa, "elementos": [{"tipo": "script", "x": 1}]}
    assert cliente.post("/api/yo/notas", json={"texto": "x", "imagen": dato, "capa": malo}, headers=X).status_code == 400


def test_tablero_de_la_reunion(cliente, cuentas, monkeypatch):
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "ana@gmail.com")
    q = cliente.post("/api/yo/notas", json={"texto": "¿por qué falló la mínima?", "tipo": "pregunta"}, headers=X).json()["id"]
    estado = lambda: [n for n in cliente.get("/api/yo/notas?tipo=pregunta").json() if n["id"] == q][0]
    assert estado()["estado"] == "propuesta"
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "beto@gmail.com")              # ni autor ni coordinador
    assert cliente.put(f"/api/yo/notas/{q}/reunion", json={"estado": "discusion"}, headers=X).status_code == 404
    cliente.cookies.clear()
    ingresar(cliente, cuentas, monkeypatch, "coord@gmail.com")
    assert cliente.put(f"/api/yo/notas/{q}/reunion", json={"estado": "discusion"}, headers=X).status_code == 200
    assert (estado()["estado"], estado()["tratada"]) == ("discusion", False)
    cliente.put(f"/api/yo/notas/{q}/reunion", json={"estado": "tratada", "respuesta": "nubes altas, no bajas"}, headers=X)
    e = estado()
    assert (e["estado"], e["tratada"], e["respuesta"], e["tratada_por"]) == ("tratada", True, "nubes altas, no bajas", "Coord")
    assert cliente.put(f"/api/yo/notas/{q}/reunion", json={"estado": "x"}, headers=X).status_code == 400
