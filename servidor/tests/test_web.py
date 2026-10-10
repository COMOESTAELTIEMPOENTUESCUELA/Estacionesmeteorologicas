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
