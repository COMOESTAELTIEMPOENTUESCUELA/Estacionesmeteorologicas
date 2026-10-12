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
