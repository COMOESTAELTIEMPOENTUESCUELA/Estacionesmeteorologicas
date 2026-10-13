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
    panel del pronóstico, sin nombres; todo lo demás, 404."""
    from meteo.web import app as web
    monkeypatch.setattr(web, "WEB_MODO", "cartas")
    assert cliente.get("/api/modo").json() == {"modo": "cartas"}
    for ruta in ("/", "/api/cartas/disponibles", "/api/cartas?ts=2026-10-12T12:00Z",
                 "/api/verificacion/dia?fecha=2026-10-10", "/vendor/chart-4.4.1.umd.js"):
        assert cliente.get(ruta).status_code == 200, ruta
    for ruta in ("/api/estaciones", "/api/emas/resumen", "/api/verificacion?ciudad=La%20Plata",
                 "/api/sinoptica/omm_87593/observaciones", "/api/estado", "/vendor/../api/estaciones",
                 "/api/cartasXX", "/docs", "/openapi.json"):
        assert cliente.get(ruta).status_code == 404, ruta
    dia = cliente.get("/api/verificacion/dia", params={"fecha": "2026-10-10"}).json()
    assert all("pronosticador" not in (c["pronostico"] or {}) for c in dia["ciudades"])
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
        from meteo.web.app import pool_dibujos
        with pool_dibujos.connection() as conn:
            conn.execute("DELETE FROM carta_dibujo WHERE producto = 'b_rv'")
