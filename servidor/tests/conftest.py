"""Lo compartido por los tests."""
import os

import pytest


@pytest.fixture(scope="session")
def _app_cliente():
    """La app web con una base real, UNA vez para todos los tests: el pool de
    conexiones no se puede volver a abrir después de cerrarlo."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("sin TEST_DATABASE_URL")
    os.environ["DATABASE_URL"] = dsn
    from fastapi.testclient import TestClient

    from meteo.web.app import app
    with TestClient(app, base_url="https://prueba.ts.net") as c:
        yield c


@pytest.fixture
def cliente(_app_cliente):
    """El cliente, con las tablas propias de la web creadas: test_db.py
    rehace el esquema entero y se las lleva (la web las crea al arrancar)."""
    from meteo.web.app import TABLAS_PROPIAS, pool_escritura
    with pool_escritura.connection() as conn:
        for sql in TABLAS_PROPIAS:
            conn.execute(sql.read_text(encoding="utf-8"))
    return _app_cliente
