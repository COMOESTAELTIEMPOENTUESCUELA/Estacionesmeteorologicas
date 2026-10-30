"""Tests de la API web contra una base real (se saltean sin TEST_DATABASE_URL)."""
import os

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def cliente():
    os.environ["DATABASE_URL"] = DSN
    from fastapi.testclient import TestClient

    from meteo.web.app import app
    with TestClient(app) as c:
        yield c


def test_estaciones(cliente):
    r = cliente.get("/api/estaciones")
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()}
    assert {"bavio", "omm_87593"} <= ids


def test_series_valida_parametros(cliente):
    assert cliente.get("/api/series", params={"variable": "nada", "estacion": "bavio"}).status_code == 404
    muchas = [("estacion", f"e{i}") for i in range(9)] + [("variable", "temp")]
    assert cliente.get("/api/series", params=muchas).status_code == 400


def test_series_y_csv(cliente):
    r = cliente.get("/api/series", params={"variable": "temp", "estacion": "bavio", "dias": 2})
    assert r.status_code == 200 and r.json()["paso"] == "crudo"
    csv = cliente.get("/api/series", params={"variable": "temp", "estacion": "bavio", "formato": "csv"})
    assert csv.text.startswith("estacion_id,ts,valor")


def test_api_es_solo_lectura(cliente):
    # La conexión de la API está en modo solo lectura: un INSERT tiene que fallar.
    from meteo.web.app import pool
    with pool.connection() as conn, conn.cursor() as cur:
        with pytest.raises(Exception, match="read-only"):
            cur.execute("INSERT INTO evento (titulo, inicio, fin) VALUES ('x', now(), now())")


def test_pagina(cliente):
    assert "Red Meteorológica" in cliente.get("/").text


def test_meteograma_ema(cliente):
    r = cliente.get("/api/estacion/bavio/meteograma", params={"dias": 2})
    assert r.status_code == 200
    d = r.json()
    assert d["paso"] == "crudo" and isinstance(d["series"], dict)


def test_resumen_emas_solo_emas(cliente):
    filas = cliente.get("/api/emas/resumen").json()
    ids = {f["id"] for f in filas}
    assert "bavio" in ids and not any(i.startswith("omm_") for i in ids)


def test_observaciones_sinoptica_calcula_humedad(cliente):
    from datetime import datetime, timedelta, timezone

    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    t = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, [Obs(t, "omm_87593", "temp", 20.0), Obs(t, "omm_87593", "td", 15.7),
                                    Obs(t, "omm_87593", "ww", 61)], "test")
    conn.close()
    filas = cliente.get("/api/sinoptica/omm_87593/observaciones", params={"dias": 0.5}).json()
    [fila] = [f for f in filas if f["ts"] == t.strftime("%Y-%m-%dT%H:%M:%SZ")]
    assert fila["hum"] == 76 and fila["ww"] == 61     # HR de Magnus con T=20, Td=15,7
    csv = cliente.get("/api/sinoptica/omm_87593/observaciones", params={"dias": 0.5, "formato": "csv"}).text
    assert csv.startswith("ts,temp,td,hum")


def test_resumen_sinopticas(cliente):
    filas = cliente.get("/api/sinopticas/resumen").json()
    assert all(f["id"].startswith("omm_") for f in filas)


def test_direccion_del_viento_promedio_circular(cliente):
    """350° y 10° son vientos casi del norte: el promedio tiene que dar ~0°, no 180°."""
    from datetime import datetime, timedelta, timezone

    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    h = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=5)
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, [Obs(h + timedelta(minutes=10), "observatorio_campbell", "viento_dir", 350.0),
                                    Obs(h + timedelta(minutes=20), "observatorio_campbell", "viento_dir", 10.0)], "test")
    conn.close()
    d = cliente.get("/api/series", params={"variable": "viento_dir", "estacion": "observatorio_campbell",
                                           "dias": 10, "paso": "hora"}).json()
    [valor] = [v for t, v in d["series"][0]["puntos"] if t == h.strftime("%Y-%m-%dT%H:%M:%SZ")]
    assert min(valor, 360 - valor) < 0.01


def test_verificacion(cliente):
    # Datos cargados por test_db.test_verificacion_de_punta_a_punta.
    r = cliente.get("/api/verificacion", params={"ciudad": "Tandil", "desde": "2026-10-01", "hasta": "2026-10-11"})
    assert r.status_code == 200
    d = r.json()
    [dia] = d["dias"]
    assert dia["plazo"] == 1 and dia["tmax_urbana"] == {"pron": 19, "obs": 18.0, "pers": None, "estacion": "omm_87645", "periodo": None}
    assert dia["pp"]["pron"] is True and dia["pp"]["obs"] is True
    assert dia["viento_dir"]["obs"] == "SW" and dia["viento_int"]["pron"] == "MODERADOS"
    assert d["resumen"]["1"]["tmax_urbana"]["mae"] == 1.0
    assert "Tandil" in cliente.get("/api/verificacion/ciudades").json()
    csv = cliente.get("/api/verificacion", params={"ciudad": "Tandil", "desde": "2026-10-01",
                                                    "hasta": "2026-10-11", "formato": "csv"})
    assert csv.text.startswith("ciudad,fecha,plazo")
    assert cliente.get("/api/verificacion", params={"version": "otra"}).status_code == 400


def test_verificacion_por_publicacion(cliente):
    q = {"ciudad": "Tandil", "desde": "2026-10-01", "hasta": "2026-10-11"}
    # La emisión de Tandil del test_db la "capturó" el servidor hoy, después de su fecha: no es anticipada.
    assert len(cliente.get("/api/verificacion", params={**q, "publicacion": "en_el_dia"}).json()["dias"]) == 1
    assert cliente.get("/api/verificacion", params={**q, "publicacion": "noche_anterior"}).json()["dias"] == []
    assert cliente.get("/api/verificacion", params={**q, "publicacion": "otra"}).status_code == 400


def test_contrasena_opcional(cliente, monkeypatch):
    import base64
    from meteo.web import app as modulo
    assert cliente.get("/api/variables").status_code == 200          # sin clave configurada: abierta
    monkeypatch.setattr(modulo, "WEB_USUARIO", "grupo")
    monkeypatch.setattr(modulo, "WEB_CLAVE", "ñandú-2026")
    async def sin_espera(_segundos):
        return None
    monkeypatch.setattr(modulo.asyncio, "sleep", sin_espera)
    r = cliente.get("/api/variables")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    mal = base64.b64encode("grupo:otra".encode()).decode()
    assert cliente.get("/", headers={"Authorization": f"Basic {mal}"}).status_code == 401
    bien = base64.b64encode("grupo:ñandú-2026".encode()).decode()
    assert cliente.get("/api/variables", headers={"Authorization": f"Basic {bien}"}).status_code == 200


def test_verificacion_etiquetas_y_semaforo(cliente):
    q = {"ciudad": "Tandil", "desde": "2026-10-01", "hasta": "2026-10-11"}
    d = cliente.get("/api/verificacion", params=q).json()
    assert "frente_frio" in d["etiquetas_def"] and d["umbrales"]["helada_c"] == 0
    assert d["resumen"]["1"]["tmax_urbana"]["nivel"] == "exploratorio"     # 1 día
    assert d["dias"][0]["etiquetas"] is not None                            # el día se etiquetó
    assert cliente.get("/api/verificacion", params={**q, "etiqueta": "sudestada"}).json()["dias"] == []
    assert cliente.get("/api/verificacion", params={**q, "etiqueta": "otra"}).status_code == 400


def test_cartas_con_fondo(cliente, tmp_path, monkeypatch):
    """Los mapas de Ogimet llevan la geografía de fondo; los de Bentley no."""
    from datetime import datetime, timezone

    from meteo.cartas import archivo
    from meteo.web import app as web
    cfg = tmp_path / "cartas.yaml"
    cfg.write_text("productos:\n"
                   "  - {id: og_te, nombre: T, fuente: ogimet, zona: amersurs, codigo: Te}\n"
                   "  - {id: b_rv, nombre: V, fuente: bentley, dominio: southamer, variable: rel_vort}\n")
    monkeypatch.setattr(web, "CARTAS_CONFIG", str(cfg))
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    # La carpeta de /cartas-img se fija al arrancar la app: se apunta a la del test.
    estaticos = next(r.app for r in web.app.routes if getattr(r, "path", "") == "/cartas-img")
    monkeypatch.setattr(estaticos, "directory", str(tmp_path / "cartas"))
    monkeypatch.setattr(estaticos, "all_directories", [str(tmp_path / "cartas")])
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    ts = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    archivo.guardar(tmp_path, "og_te", ts, png)
    archivo.guardar_fondo(tmp_path, "ogimet_amersurs", png)
    p = {x["id"]: x for x in cliente.get("/api/cartas", params={"ts": "2026-10-12T12:00Z"}).json()["productos"]}
    assert p["og_te"]["url"] == "/cartas-img/og_te/2026/10/og_te_2026101212.png"
    assert p["og_te"]["fondo"] == "/cartas-img/_fondos/ogimet_amersurs.png"
    assert p["b_rv"]["fondo"] is None and p["b_rv"]["url"] is None
    assert p["og_te"]["miniatura"] is None                         # liviana: va tal cual
    img = cliente.get(p["og_te"]["url"])
    assert img.status_code == 200 and "immutable" in img.headers["cache-control"]   # el navegador la guarda
    assert "immutable" not in cliente.get(p["og_te"]["fondo"]).headers["cache-control"]
    assert "cache-control" not in cliente.get("/api/estaciones").headers


def test_verificacion_dia(cliente):
    r = cliente.get("/api/verificacion/dia", params={"fecha": "2026-10-12"})
    assert r.status_code == 200
    c = r.json()["ciudades"]
    assert c[0]["ciudad"] == "La Plata" and c[0]["coord"] and "Bolívar" not in {x["ciudad"] for x in c}
    assert c[0]["synop"][0] == "omm_87593"                          # el Aero, para el link a Ogimet
    assert cliente.get("/api/verificacion/dia", params={"fecha": "2026-10-12", "plazo": 9}).status_code == 400


def test_periodos_por_fecha(cliente):
    """Un día o un período del pasado (para eventos), además de "los últimos N días"."""
    dia = {"desde": "2026-10-09T03:00:00Z", "hasta": "2026-10-10T03:00:00Z"}     # 9/10, 00 a 24 hs de Argentina
    r = cliente.get("/api/estacion/bavio/meteograma", params=dia)
    assert r.status_code == 200 and r.json()["desde"].startswith("2026-10-09T03:00") and r.json()["paso"] == "crudo"
    largo = cliente.get("/api/estacion/bavio/meteograma", params={"desde": "2026-09-01T03:00:00Z", "hasta": "2026-09-11T03:00:00Z"})
    assert largo.json()["paso"] == "hora"                                         # más de 3 días: promedios horarios
    assert cliente.get("/api/sinoptica/omm_87593/observaciones", params=dia).status_code == 200
    csv = cliente.get("/api/sinoptica/omm_87593/observaciones", params={**dia, "formato": "csv"})
    assert "2026100903_2026101003" in csv.headers["content-disposition"]
    al_reves = {"desde": dia["hasta"], "hasta": dia["desde"]}
    assert cliente.get("/api/sinoptica/omm_87593/observaciones", params=al_reves).status_code == 400
    muy_largo = {"desde": "2026-01-01T00:00:00Z", "hasta": "2026-03-01T00:00:00Z"}
    assert cliente.get("/api/sinoptica/omm_87593/observaciones", params=muy_largo).status_code == 400
    assert cliente.get("/api/sinoptica/omm_87593/observaciones", params={"dias": 2}).status_code == 200


def test_modo_cartas_solo_expone_las_cartas(cliente, monkeypatch):
    """La copia pública (WEB_MODO=cartas) responde la página, las cartas y el
    panel del pronóstico (con quién pronosticó); todo lo demás, 404."""
    from meteo.web import app as web
    monkeypatch.setattr(web, "WEB_MODO", "cartas")
    assert cliente.get("/api/modo").json() == {"modo": "cartas"}
    for ruta in ("/", "/api/cartas/disponibles", "/api/cartas?ts=2026-10-12T12:00Z",
                 "/api/verificacion/dia?fecha=2026-10-10", "/vendor/chart-4.4.1.umd.js",
                 "/api/cartas/estaciones-omm"):
        assert cliente.get(ruta).status_code == 200, ruta
    for ruta in ("/api/estaciones", "/api/emas/resumen", "/api/verificacion?ciudad=La%20Plata",
                 "/api/sinoptica/omm_87593/observaciones", "/api/estado", "/vendor/../api/estaciones",
                 "/api/cartasXX", "/docs", "/openapi.json"):
        assert cliente.get(ruta).status_code == 404, ruta
    dia = cliente.get("/api/verificacion/dia", params={"fecha": "2026-10-10"}).json()
    assert any("pronosticador" in (c["pronostico"] or {}) for c in dia["ciudades"])   # el grupo decidió mostrarlo
    monkeypatch.setattr(web, "WEB_MODO", "completo")
    assert cliente.get("/api/estaciones").status_code == 200


def test_dibujos_sobre_las_cartas(cliente, tmp_path, monkeypatch):
    """Se guardan por carta y hora, vienen con /api/cartas, se validan, se
    borran al quedar vacíos, y la copia pública no puede escribir."""
    from meteo.web import app as web
    cfg = tmp_path / "cartas.yaml"
    cfg.write_text("productos:\n  - {id: b_rv, nombre: V, fuente: bentley, dominio: southamer, variable: rel_vort}\n")
    monkeypatch.setattr(web, "CARTAS_CONFIG", str(cfg))
    q = {"producto": "b_rv", "ts": "2026-10-12T12:00:00Z"}
    d = {"ancho": 1000, "alto": 750, "elementos": [
        {"tipo": "frente", "clase": "frio", "lado": 1, "puntos": [[100, 100], [300, 250]]},
        {"tipo": "centro", "letra": "B", "x": 500, "y": 400}]}
    try:
        assert cliente.put("/api/cartas/dibujo", params=q, json=d).json() == {"ok": True, "elementos": 2}
        assert cliente.get("/api/cartas/dibujo", params=q).json()["dibujo"]["elementos"][1]["letra"] == "B"
        assert cliente.get("/api/cartas", params={"ts": "2026-10-12T12:00Z"}).json()["productos"][0]["dibujo"]["alto"] == 750
        malo = {**d, "elementos": [{"tipo": "<script>"}]}
        assert cliente.put("/api/cartas/dibujo", params=q, json=malo).status_code == 400
        assert cliente.put("/api/cartas/dibujo", params={**q, "producto": "otra"}, json=d).status_code == 404
        monkeypatch.setattr(web, "WEB_MODO", "cartas")
        assert cliente.put("/api/cartas/dibujo", params=q, json=d).status_code == 405   # copia pública: solo mirar
        assert cliente.get("/api/cartas/dibujo", params=q).json()["dibujo"] is not None  # pero se ve
        monkeypatch.setattr(web, "WEB_MODO", "completo")
        assert cliente.put("/api/cartas/dibujo", params=q, json={**d, "elementos": []}).json()["elementos"] == 0
        assert cliente.get("/api/cartas/dibujo", params=q).json()["dibujo"] is None     # vacío = borrado
    finally:
        from meteo.web.app import pool_escritura
        with pool_escritura.connection() as conn:
            conn.execute("DELETE FROM carta_dibujo WHERE producto = 'b_rv'")


def test_observaciones_de_la_hora_de_la_carta(cliente):
    """El SYNOP de esa hora de cada estación del SMN (sin EMAs)."""
    from datetime import datetime, timedelta, timezone

    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    t = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, [
        Obs(t, "omm_87593", "temp", 12.0), Obs(t, "omm_87593", "td", 8.0), Obs(t, "omm_87593", "tmax", 19.5),
        Obs(t - timedelta(hours=1), "omm_87593", "temp", 99.0),                      # otra hora: no va
        Obs(t, "bavio", "temp", 13.0)], "test")                                       # EMA: no va
    conn.close()
    r = cliente.get("/api/cartas/observaciones", params={"ts": "2026-09-03T12:00:00Z"}).json()
    [aero] = [f for f in r["smn"] if f["id"] == "omm_87593"]
    assert (aero["temp"], aero["tmax"], aero["hum"]) == (12.0, 19.5, 77)
    assert set(r) == {"ts", "smn"} and not any(f["id"] == "bavio" for f in r["smn"])


def test_cortes_de_una_estacion():
    """Huecos -> cortes: recorta al período, agrega el corte que sigue abierto
    y no cuenta como caída el tiempo antes de que la estación existiera."""
    from datetime import datetime, timedelta, timezone

    from meteo.web.app import _restar, cortes_de_estacion
    t = lambda h: datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=h)
    u = timedelta(hours=1)
    inicio, c = cortes_de_estacion([(t(-5), t(3)), (t(10), t(16))], t(-20), t(40), True, t(0), t(48), u)
    assert inicio == t(0) and c == [(t(0), t(3), False), (t(10), t(16), False), (t(40), t(48), True)]
    inicio, c = cortes_de_estacion([], t(5), t(48), False, t(0), t(48), u)   # empezó a las 5: no estaba caída
    assert inicio == t(5) and c == []
    assert cortes_de_estacion([], None, None, True, t(0), t(48), u) == (t(0), [(t(0), t(48), True)])
    assert cortes_de_estacion([], None, None, False, t(0), t(48), u) == (None, [])
    quedan, sacado = _restar([(t(0), t(10))], t(2), t(4))
    assert quedan == [(t(0), t(2)), (t(4), t(10))] and sacado == timedelta(hours=2)


def test_bitacora_y_disponibilidad(cliente, monkeypatch):
    """Se anota la causa de un corte y la disponibilidad la usa para explicar
    las horas perdidas; la copia pública no puede escribir ni ver la bitácora."""
    from datetime import datetime, timedelta, timezone

    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    from meteo.web import app as web
    t0 = datetime(2026, 8, 1, 3, tzinfo=timezone.utc)          # 00 hora argentina
    obs = [Obs(t0 + timedelta(minutes=10 * i), "bavio", "temp", 15.0) for i in range(-6, 6 * 48)
           if not 60 <= i < 96]                                  # hueco: de 12:50 a 19:00 UTC
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, obs, "test")
    conn.close()
    rango = {"desde": "2026-08-01T03:00:00Z", "hasta": "2026-08-03T03:00:00Z"}
    nuevo = {"estacion_id": "bavio", "causa": "internet", "desde": "2026-08-01T10:00:00-03:00",
             "hasta": "2026-08-01T14:00:00-03:00", "descripcion": "Se cortó el WiFi de la escuela"}
    ids = []
    try:
        assert cliente.post("/api/incidentes", json={**nuevo, "causa": "nada"}).status_code == 400
        assert cliente.post("/api/incidentes", json={**nuevo, "hasta": "2026-08-01T09:00:00-03:00"}).status_code == 400
        r = cliente.post("/api/incidentes", json=nuevo).json()
        ids.append(r["id"])
        d = cliente.get("/api/estaciones/disponibilidad", params=rango).json()
        [b] = [e for e in d["estaciones"] if e["id"] == "bavio"]
        assert b["cortes"] == 1 and b["horas_sin_datos"] == 6.2           # de 12:50 a 19:00 UTC
        assert b["por_causa"] == {"internet": 4.0} and b["horas_sin_explicar"] == 2.2
        assert 85 < b["disponibilidad"] < 88
        assert b["lista_cortes"][0]["causas"] == [{"id": r["id"], "causa": "internet"}]
        # Se corrige: duró hasta que volvió. Los 10 min antes del inicio
        # anotado (el último dato fue 12:50 UTC) se le suman a la misma causa.
        assert cliente.put(f"/api/incidentes/{r['id']}", json={**nuevo, "desde": "2026-08-01T10:00:00-03:00",
                                                                 "hasta": "2026-08-01T16:00:00-03:00"}).json()["ok"]
        d = cliente.get("/api/estaciones/disponibilidad", params=rango).json()
        [b] = [e for e in d["estaciones"] if e["id"] == "bavio"]
        assert b["horas_sin_explicar"] == 0 and b["por_causa"] == {"internet": 6.2}
        lista = cliente.get("/api/incidentes", params=rango).json()
        assert [i["descripcion"] for i in lista] == ["Se cortó el WiFi de la escuela"]
        monkeypatch.setattr(web, "WEB_MODO", "cartas")
        assert cliente.post("/api/incidentes", json=nuevo).status_code == 404     # ahí ni existe
        assert cliente.get("/api/incidentes", params=rango).status_code == 404
        monkeypatch.setattr(web, "WEB_MODO", "completo")
        assert cliente.delete(f"/api/incidentes/{r['id']}").json()["ok"]
        assert cliente.delete(f"/api/incidentes/{r['id']}").status_code == 404
        ids.clear()
    finally:
        with web.pool_escritura.connection() as conn:
            for i in ids:
                conn.execute("DELETE FROM estacion_incidente WHERE id = %s", (i,))
            conn.execute("DELETE FROM observacion WHERE estacion_id = 'bavio' AND ts BETWEEN %s AND %s",
                         (t0 - timedelta(hours=2), t0 + timedelta(days=2)))


def test_lluvia_por_fechas(cliente):
    """La lluvia diaria se puede pedir de un período (para mirar un evento)."""
    r = cliente.get("/api/lluvia", params={"desde": "2026-10-05", "hasta": "2026-10-10"})
    assert r.status_code == 200
    assert all("2026-10-05" <= f["dia"] <= "2026-10-10" for f in r.json())
    assert cliente.get("/api/lluvia", params={"desde": "2025-01-01", "hasta": "2026-10-10"}).status_code == 400
    csv = cliente.get("/api/lluvia", params={"desde": "2026-10-05", "hasta": "2026-10-10", "formato": "csv"})
    assert "lluvia_diaria_2026-10-05_2026-10-10" in csv.headers["content-disposition"]


def test_synop_del_dia_para_el_grafico_de_cartas(cliente, monkeypatch):
    """Lo observado por una sinóptica en un día argentino (00 a 24 hs). Está en
    la copia pública, así que no entrega EMAs."""
    from datetime import datetime, timedelta, timezone

    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    from meteo.web import app as web
    t0 = datetime(2026, 9, 5, 3, tzinfo=timezone.utc)                        # 00 hs del 5/9 en Argentina
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, [Obs(t0 + timedelta(hours=h), "omm_87593", "temp", 10.0 + h) for h in range(0, 24, 3)]
                             + [Obs(t0 - timedelta(hours=1), "omm_87593", "temp", 99.0)], "test")   # del día anterior: no va
    conn.close()
    try:
        monkeypatch.setattr(web, "WEB_MODO", "cartas")
        d = cliente.get("/api/cartas/synop", params={"estacion": "omm_87593", "fecha": "2026-09-05"}).json()
        assert [f["temp"] for f in d["filas"]] == [10.0 + h for h in range(0, 24, 3)]   # en orden, sin el 99
        assert d["desde"] == "2026-09-05T03:00:00Z" and "mensaje" not in d["filas"][0]
        assert cliente.get("/api/cartas/synop", params={"estacion": "bavio", "fecha": "2026-09-05"}).status_code == 404
    finally:
        monkeypatch.setattr(web, "WEB_MODO", "completo")
        with web.pool_escritura.connection() as conn:
            conn.execute("DELETE FROM observacion WHERE estacion_id = 'omm_87593' AND ts BETWEEN %s AND %s",
                         (t0 - timedelta(hours=2), t0 + timedelta(days=1)))


def test_api_pronostico_gfs(cliente, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from meteo.cartas import pronostico
    from meteo.web import app as web
    cfg = tmp_path / "cartas.yaml"
    cfg.write_text("productos:\n  - {id: b_vort, nombre: Vort, nivel: '500 hPa', fuente: bentley, dominio: southamer, variable: rel_vort}\n"
                   "  - {id: b_viejo, nombre: V, fuente: bentley, dominio: southamer, variable: x, descargar: false}\n"
                   "  - {id: b_sin, nombre: S, fuente: bentley, dominio: southamer, variable: y, pronostico: false}\n"
                   "  - {id: g_ivt, nombre: IVT, nivel: '700 hPa', fuente: gfs_pronostico, carta: ivt}\n")
    monkeypatch.setattr(web, "CARTAS_CONFIG", str(cfg))
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    vacio = cliente.get("/api/cartas/pronostico").json()
    assert vacio["productos"] == [] and vacio["ciudades"]["La Plata"] == {"lat": -34.92, "lon": -57.95}
    ini = datetime(2026, 10, 12, 6, tzinfo=timezone.utc)
    for pid in ("b_vort", "b_viejo", "b_sin", "g_ivt"):
        tmp = pronostico.empezar(tmp_path, pid)
        pronostico.publicar(tmp_path, pid, tmp, ini, [(6, datetime(2026, 10, 12, 12, tzinfo=timezone.utc), "a.png")])
    monkeypatch.setattr(web, "WEB_MODO", "cartas")          # también en la copia pública
    d = cliente.get("/api/cartas/pronostico").json()["productos"]
    monkeypatch.setattr(web, "WEB_MODO", "completo")
    assert [p["id"] for p in d] == ["b_vort", "g_ivt"]      # las que ya no se bajan (o sin pronóstico), no
    # Las del pronóstico propio no aparecen entre las cartas archivadas por hora.
    assert "g_ivt" not in [p["id"] for p in web._cartas_productos()]
    assert d[0]["cuadros"][0]["url"] == "/cartas-img/_pronostico/b_vort/a.png" and d[0]["nivel"] == "500 hPa"
    g = d[1]
    assert g["plantilla"] == "/cartas-img/_pronostico/g_ivt/g_ivt_{corrida}_f{plazo}.png"
    assert g["corridas"] == [{"inicio": "2026-10-12T06:00:00+00:00", "plazos": [6]}]
    assert cliente.get("/api/cartas/pronostico?corrida=2026101206").json()["productos"][1]["id"] == "g_ivt"
    assert cliente.get("/api/cartas/pronostico?corrida=2026101200").json()["productos"] == []
    assert cliente.get("/api/cartas/pronostico?corrida=xx").status_code == 400


def test_api_punto_meteograma(cliente, tmp_path, monkeypatch):
    """Meteograma de diagnóstico: los valores del GFS de cada 6 h del período
    (sin el perfil, que es para el sondeo) y lo medido por su estación."""
    from datetime import datetime, timezone

    from meteo.cartas import puntos
    from meteo.web import app as web
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    for h in (0, 6, 12):
        puntos.guardar(tmp_path, datetime(2026, 10, 28, h, tzinfo=timezone.utc),
                       {"La Plata": {"pnm": 1010 + h, "perfil": {"p": [1000]}}, "Junín": {"pnm": 1000}})
    r = cliente.get("/api/cartas/punto?ciudad=La Plata&desde=2026-10-28T03:00Z&hasta=2026-10-28T13:00Z")
    assert r.status_code == 200
    d = r.json()
    assert [(g["ts"], g["pnm"], g["perfil"]) for g in d["gfs"]] == [("2026-10-28T06:00:00Z", 1016, True), ("2026-10-28T12:00:00Z", 1022, True)]
    assert d["estacion"] == "omm_87593" and "Ezeiza" in d["ciudades"]
    con = cliente.get("/api/cartas/punto?ciudad=La Plata&desde=2026-10-28T03:00Z&hasta=2026-10-28T13:00Z&perfiles=true").json()
    assert con["gfs"][0]["perfil"] == {"p": [1000]}
    assert cliente.get("/api/cartas/punto?ciudad=Narnia&desde=2026-10-28T03:00Z&hasta=2026-10-28T13:00Z").status_code == 404
    assert cliente.get("/api/cartas/punto?ciudad=La Plata&desde=2026-10-01T00:00Z&hasta=2026-10-28T13:00Z").status_code == 400


def test_api_sondeo(cliente, tmp_path, monkeypatch):
    """Sondeo del GFS (con la superficie del modelo abajo) y el real de Ezeiza
    de esa hora, cada uno con sus índices."""
    from datetime import datetime, timezone

    from meteo.cartas import puntos, sondeos
    from meteo.web import app as web
    cfg = tmp_path / "cartas.yaml"
    cfg.write_text('sondeos:\n  - {id: "87576", igra: ARM00087576, nombre: Ezeiza}\nproductos: []\n')
    monkeypatch.setattr(web, "CARTAS_CONFIG", str(cfg))
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    ts = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    p = [1000, 950, 900, 850, 700, 500, 300]
    puntos.guardar(tmp_path, ts, {"Ezeiza": {
        "sup": {"p": 1015, "z": 20, "t": 22, "td": 15, "v": [8, 40]}, "cape": 300, "cin": -10, "pw": 25,
        "perfil": {"p": p, "z": [110, 540, 990, 1460, 3050, 5750, 9400], "t": [20, 17, 14, 11, 2, -14, -40],
                   "td": [14, 12, 10, 6, -6, -25, -50], "dir": [30, 0, 330, 300, 280, 270, 270], "kt": [10, 15, 20, 25, 35, 50, 80]}}})
    sondeos.guardar(tmp_path, "87576", {"ts": ts, "niveles": [{"p": a, "z": None, "t": b, "td": b - 5, "dir": 0, "kt": 10}
                                                             for a, b in zip(p, [19, 16, 13, 10, 1, -15, -41])]})
    d = cliente.get("/api/cartas/sondeo?ciudad=Ezeiza&ts=2026-10-29T12:00Z").json()
    assert d["gfs"]["niveles"][0] == {"p": 1015, "z": 20, "t": 22, "td": 15, "dir": 40, "kt": 8}
    assert len(d["gfs"]["niveles"]) == 8 and d["gfs"]["modelo"] == {"cape": 300, "cin": -10, "pw": 25}
    assert "cape" in d["gfs"]["indices"] and d["real"]["nombre"] == "Ezeiza" and "pw" in d["real"]["indices"]
    sin = cliente.get("/api/cartas/sondeo?ciudad=La Plata&ts=2026-10-29T18:00Z").json()
    assert sin["gfs"] is None and sin["real"] is None
