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
