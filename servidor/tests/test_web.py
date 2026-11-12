"""Tests de la API web contra una base real (se saltean sin TEST_DATABASE_URL)."""
import os

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin TEST_DATABASE_URL")


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
    assert "Meteo UNLP" in cliente.get("/").text


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
                 "/api/cartas/estaciones-omm", "/placa.html", "/placa/Tmax.png"):
        assert cliente.get(ruta).status_code == 200, ruta
    for ruta in ("/api/estaciones", "/api/emas/resumen", "/api/verificacion?ciudad=La%20Plata",
                 "/api/sinoptica/omm_87593/observaciones", "/api/estado", "/vendor/../api/estaciones",
                 "/api/cartasXX", "/docs", "/openapi.json", "/placaXX", "/placa/../api/estaciones"):
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


def test_api_meteograma_del_pronostico(cliente, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from meteo.cartas import puntos
    from meteo.web import app as web
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    assert cliente.get("/api/cartas/pronostico/punto?ciudad=La Plata").json()["plazos"] == []
    ini = datetime(2026, 11, 1, 0, tzinfo=timezone.utc)
    puntos.guardar_pronostico(tmp_path, ini, {0: {"La Plata": {"pnm": 1015}}, 6: {"La Plata": {"pnm": 1012, "pp6": 3.5}}})
    d = cliente.get("/api/cartas/pronostico/punto?ciudad=La Plata").json()
    assert d["inicio"] == "2026-11-01T00:00:00Z"
    assert d["plazos"][1] == {"plazo": 6, "valida": "2026-11-01T06:00:00Z", "pnm": 1012, "pp6": 3.5}
    assert cliente.get("/api/cartas/pronostico/punto?ciudad=La Plata&corrida=2026110100").json()["plazos"][0]["pnm"] == 1015
    assert cliente.get("/api/cartas/pronostico/punto?ciudad=Narnia").status_code == 404


def test_georef_de_las_cartas(cliente, tmp_path, monkeypatch):
    """Coordenadas para medir en el visor: Mercator (estilo Bentley), lineal
    calculada para los mapas propios (rayos) y la que viene en la config."""
    from meteo.cartas import estilo_bentley
    from meteo.satelite import render
    from meteo.web import app as web
    cfg = tmp_path / "cartas.yaml"
    cfg.write_text("""productos:
  - {id: gfs_ana_mslp_jet, fuente: gfs_analisis, carta: mslp_jet, nombre: x}
  - {id: rayos, fuente: glm, nombre: r, region: [-64.0, -53.0, -41.3, -30.0]}
  - {id: sat, fuente: star, nombre: s, sector: ssa, banda: "13"}
  - {id: sat2, fuente: star, nombre: s2, georef: {tipo: lineal, ax: 0.02, bx: 2.0, ay: -0.02, by: -0.1, y: lat}}
""", encoding="utf-8")
    monkeypatch.setattr(web, "CARTAS_CONFIG", str(cfg))
    web._GEOREF_CACHE.clear()
    g = cliente.get("/api/cartas/georef").json()
    assert g["gfs_ana_mslp_jet"]["tipo"] == "mercator" and g["gfs_ana_mslp_jet"]["marco"]["caja"]
    assert g["sat"] is None and g["sat2"]["ax"] == 0.02
    # La Plata en el mapa de rayos: la cuenta lineal tiene que dar lo mismo que matplotlib.
    r = g["rayos"]
    fx, fy = r["ax"] * -57.95 + r["bx"], r["ay"] * -34.92 + r["by"]
    m = render.marco([-64.0, -53.0, -41.3, -30.0])
    (c0, c1, c2, c3), (lo0, lo1, la0, la1) = m["caja"], m["region"]
    assert abs(fx - (c0 + (-57.95 - lo0) / (lo1 - lo0) * (c2 - c0))) < 1e-6
    assert abs(fy - (c1 + (la1 - -34.92) / (la1 - la0) * (c3 - c1))) < 1e-6
    # Y el Mercator coincide con a_imagen (la misma cuenta que usa la página).
    fxm, fym = estilo_bentley.a_imagen(g["gfs_ana_mslp_jet"]["marco"], -57.95, -34.92)
    assert 0 < fxm < 1 and 0 < fym < 1


def test_app_instalable_y_compartir(cliente, monkeypatch):
    """El manifest (con share_target para el menú Compartir del celular), el
    service worker y los íconos se sirven también en la copia pública."""
    from meteo.web import app as web
    monkeypatch.setattr(web, "WEB_MODO", "cartas")
    r = cliente.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["share_target"]["params"]["url"] == "compartir_url" and m["share_target"]["action"] == "/"
    assert cliente.get("/sw.js").status_code == 200
    assert cliente.get("/iconos/icono-192.png").headers["content-type"] == "image/png"


def test_sin_funciones_repetidas_en_la_pagina():
    """En JavaScript, dos funciones con el mismo nombre no dan error: la
    segunda pisa a la primera en silencio. Pasó con rumbo(): la de las
    mediciones (dos puntos) pisó a la del viento (grados) y la tabla de
    observaciones y los gráficos de Diagnóstico quedaron vacíos."""
    import collections
    import re
    from pathlib import Path
    html = (Path(__file__).parents[1] / "app/meteo/web/static/index.html").read_text(encoding="utf-8")
    js = html[html.rindex("<script>"):]
    nombres = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", js, re.M)
    nombres += re.findall(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", js, re.M)
    repetidos = [n for n, c in collections.Counter(nombres).items() if c > 1]
    assert repetidos == [], f"nombres repetidos en el JavaScript de la página: {repetidos}"


def test_calibracion_automatica_de_ogimet_y_compartida(cliente, tmp_path, monkeypatch):
    """Los mapas de Ogimet se calibran solos con las estaciones (cada cartel
    trae latitud y longitud); las calibraciones a mano quedan para todos."""
    import json as js
    import math

    from PIL import Image

    from meteo.web import app as A
    # Un mapa "Mercator" de 800x600 con 6 estaciones en su lugar exacto.
    ax, bx, ay, by = 0.02, 1.7, -1.5, 0.05
    estaciones = [(-57.9, -34.97), (-58.5, -34.8), (-62.2, -38.7), (-64.2, -31.3), (-68.8, -32.8), (-56.0, -34.8)]
    def dms(v, pos, neg, ancho):
        a = abs(v); g = int(a); m = int((a - g) * 60); s = round(((a - g) * 60 - m) * 60)
        return f"{g:0{ancho}d}-{m:02d}-{s:02d}{pos if v >= 0 else neg}"
    zonas = []
    for lon, lat in estaciones:
        fx, fy = ax * lon + bx, ay * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) + by
        x, y = fx * 800, fy * 600
        zonas.append({"omm": "1", "caja": [x - 6, y - 4, x + 6, y + 4], "texto": f"Lat: {dms(lat, 'N', 'S', 2)} Lon: {dms(lon, 'E', 'W', 3)} Alt: 1 m.", "enlace": ""})
    carpeta = tmp_path / "cartas" / "_areas" / "og_te" / "2026" / "10"
    carpeta.mkdir(parents=True)
    (carpeta / "og_te_2026101212.json").write_text(js.dumps(zonas))
    img = tmp_path / "cartas" / "og_te" / "2026" / "10"
    img.mkdir(parents=True)
    Image.new("RGB", (800, 600)).save(img / "og_te_2026101212.png")
    monkeypatch.setattr(A, "ARCHIVO_DIR", str(tmp_path))
    from meteo.cartas import archivo
    if not archivo.ruta(str(tmp_path), "og_te", A.datetime(2026, 10, 12, 12, tzinfo=A.timezone.utc), "png").exists():
        import pytest
        pytest.skip("otra estructura de carpetas del archivo")
    g = A._georef_ogimet("og_te")
    assert g["y"] == "merc" and g["n"] == 6 and g["error_km"] < 2
    assert abs(g["ax"] - ax) < 1e-3 and abs(g["ay"] - ay) < 1e-2
    # Calibración a mano, guardada para todos (con sesión); solo números.
    from meteo.web import cuentas as m
    monkeypatch.setattr(m, "GOOGLE_CLIENT_ID", "id-prueba")
    monkeypatch.setattr(m, "GOOGLE_CLIENT_SECRET", "secreto")
    monkeypatch.setattr(m, "URL_PUBLICA", "https://prueba.ts.net")
    m.main(["agregar", "--email", "cal@gmail.com", "--nombre", "Cal"])
    from tests.test_cuentas import X, ingresar
    try:
        cliente.cookies.clear()
        ingresar(cliente, m, monkeypatch, "cal@gmail.com")
        buena = {"tipo": "lineal", "ax": 0.01, "bx": 1.2, "ay": -0.02, "by": -0.3, "y": "lat", "error_km": 12,
                 "puntos": [{"p": [1, 2], "ll": [-57.9, -34.9], "nombre": "La Plata", "alto": 750}, {"p": [5, 6], "ll": [-56.2, -34.9], "nombre": "Montevideo"}]}
        assert cliente.put("/api/yo/calibracion", json={"producto": "satelite_ir13", "georef": buena}, headers=X).status_code == 200
        assert cliente.put("/api/yo/calibracion", json={"producto": "satelite_ir13", "georef": {**buena, "ax": "1;DROP"}}, headers=X).status_code == 400
        assert cliente.put("/api/yo/calibracion", json={"producto": "../x", "georef": buena}, headers=X).status_code == 400
        guardada = A._calibraciones_compartidas()["satelite_ir13"]
        assert guardada["compartida"] and guardada["por"] == "Cal" and guardada["ax"] == 0.01 and len(guardada["puntos"]) == 2
        assert cliente.delete("/api/yo/calibracion/satelite_ir13", headers=X).status_code == 200
        assert "satelite_ir13" not in A._calibraciones_compartidas()
    finally:
        with m._pools["e"].connection() as conn:
            conn.execute("DELETE FROM carta_calibracion")
            conn.execute("DELETE FROM usuario WHERE email = 'cal@gmail.com'")
            conn.commit()


def test_todas_las_observaciones_del_dia(cliente, monkeypatch):
    """Diagnóstico: cada observación del día de cada estación de la ciudad. Las
    EMAs (datos propios) no salen en la copia pública para quien no entró."""
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    from meteo.web import app as web
    monkeypatch.setenv("ESTACIONES_CONFIG", str(Path(__file__).parents[1] / "config/estaciones.yaml"))
    t0 = datetime(2026, 9, 6, 3, tzinfo=timezone.utc)                        # 00 hs del 6/9 en Argentina
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, [Obs(t0 + timedelta(hours=h), "omm_87593", "temp", 10.0 + h) for h in range(0, 24, 3)]
                             + [Obs(t0 + timedelta(minutes=m), "observatorio_campbell", v, 12.0) for m in (0, 10) for v in ("temp", "hum")]
                             + [Obs(t0 + timedelta(days=1, hours=1), "observatorio_campbell", "temp", 99.0)], "test")   # otro día
    conn.close()
    try:
        d = cliente.get("/api/cartas/observaciones-dia", params={"ciudad": "La Plata", "fecha": "2026-09-06"}).json()
        est = {e["id"]: e for e in d["estaciones"]}
        assert [f["temp"] for f in est["omm_87593"]["filas"]] == [10.0 + h for h in range(0, 24, 3)]
        camp = est["observatorio_campbell"]
        assert len(camp["filas"]) == 2 and {c["id"] for c in camp["columnas"]} == {"temp", "hum"}
        assert d["emas_ocultas"] == 0
        assert cliente.get("/api/cartas/observaciones-dia", params={"ciudad": "Atlántida", "fecha": "2026-09-06"}).status_code == 404
        # Copia pública, sin sesión: solo los SYNOP del SMN.
        monkeypatch.setattr(web, "WEB_MODO", "cartas")
        cliente.cookies.clear()
        d = cliente.get("/api/cartas/observaciones-dia", params={"ciudad": "La Plata", "fecha": "2026-09-06"}).json()
        assert all(e["tipo"] == "sinoptica" for e in d["estaciones"]) and d["emas_ocultas"] >= 1
    finally:
        monkeypatch.setattr(web, "WEB_MODO", "completo")
        with web.pool_escritura.connection() as conn:
            conn.execute("DELETE FROM observacion WHERE estacion_id IN ('omm_87593', 'observatorio_campbell') AND ts BETWEEN %s AND %s",
                         (t0 - timedelta(hours=2), t0 + timedelta(days=2)))


def test_revisar_ema(cliente, capsys):
    """El informe de una EMA detecta huecos, valores trabados, saltos, lluvia
    en pulsos y el sesgo de la tarde (abrigo calentado por el sol)."""
    import math
    from datetime import datetime, timedelta, timezone

    from meteo.herramientas import revisar_ema
    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    from meteo.web import app as web
    ahora = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ahora = ahora.replace(minute=ahora.minute // 10 * 10)
    t0, o, t = ahora - timedelta(days=3), [], ahora - timedelta(days=3)
    while t < ahora:
        h = (t.hour - 3) % 24
        base = 18 + 7 * math.sin((h - 9) / 24 * 2 * math.pi)
        if not t0 + timedelta(days=1) < t < t0 + timedelta(days=1, hours=4):           # hueco de 4 h
            trabada = t0 + timedelta(days=2) < t < t0 + timedelta(days=2, hours=3)
            o += [Obs(t, "ema_verde", "temp", 21.3 if trabada else round(base + (2 if 12 <= h < 18 else 0), 1)),
                  Obs(t, "ema_verde", "precip", 1.0 if t0 + timedelta(hours=5) < t < t0 + timedelta(hours=6) else 0.0)]
        if t.minute == 0:
            o.append(Obs(t, "omm_87593", "temp", round(base, 1)))
        t += timedelta(minutes=10)
    conn = db.conectar(DSN)
    db.guardar_observaciones(conn, o, "test")
    conn.close()
    try:
        revisar_ema.main(["ema_verde", "--dias", "4", "--refs", "omm_87593"])
        sal = capsys.readouterr().out
        assert "Huecos de más de 30 min: 1" in sal and "trabada en 21.3" in sal
        assert "parece que manda PULSOS" in sal
        import re
        tramos = dict(re.findall(r"(\w+) \([\d-]+\)\s+(-?[\d.]+)", [l for l in sal.splitlines() if "sesgo por hora del día" in l][0]))
        assert float(tramos["tarde"]) > 1.5 and abs(float(tramos["madrugada"])) < 0.3
    finally:
        with web.pool_escritura.connection() as c:
            c.execute("DELETE FROM observacion WHERE estacion_id IN ('ema_verde', 'omm_87593') AND ts > %s", (t0 - timedelta(hours=1),))
