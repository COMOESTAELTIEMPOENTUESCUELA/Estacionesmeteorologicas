"""
Tests contra una base PostgreSQL real. Se saltean si no hay una disponible.
Para correrlos: TEST_DATABASE_URL=postgresql://... pytest
(la base tiene que estar VACÍA: el test carga el esquema).
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meteo.ingesta import db
from meteo.ingesta.base import Obs
from meteo.ingesta.fuentes import comunidad, pronosticos
from meteo.ingesta.tareas import cargar_config

RAIZ = Path(__file__).parent.parent
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def conn():
    c = db.conectar(DSN)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for sql in sorted((RAIZ / "db/init").glob("*.sql")):
            cur.execute(sql.read_text())
    c.commit()
    db.sincronizar_estaciones(c, cargar_config(RAIZ / "config/estaciones.yaml")["estaciones"])
    yield c
    c.close()


def consulta(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_estaciones_sin_claves(conn):
    [(cfg,)] = consulta(conn, "SELECT config FROM estacion WHERE id = 'bavio'")
    assert "canales" not in cfg  # las claves de API no se copian a la base
    assert consulta(conn, "SELECT count(*) FROM instrumento WHERE estacion_id='bavio'")[0][0] == 1


def test_upsert_idempotente_y_qc(conn):
    t = datetime(2026, 10, 10, 13, tzinfo=timezone.utc)
    obs = [Obs(t, "bavio", "temp", 15.0), Obs(t, "bavio", "hum", 140.0)]
    assert db.guardar_observaciones(conn, obs, "test") == 2
    assert db.guardar_observaciones(conn, obs, "test") == 0  # repetir no cambia nada
    qc = dict(consulta(conn, "SELECT variable, qc FROM observacion WHERE estacion_id='bavio'"))
    assert qc == {"temp": 1, "hum": 4}  # 140 % es imposible -> malo


def test_lluvia_diaria_9_a_9(conn):
    base = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)  # 9 hs del 9/10
    obs = [Obs(base + timedelta(hours=h), "san_vicente", "precip", 1.0) for h in (1, 10, 24, 25)]
    db.guardar_observaciones(conn, obs, "test")
    filas = dict(consulta(conn, "SELECT dia::text, lluvia_mm FROM lluvia_diaria WHERE estacion_id='san_vicente'"))
    # 13 y 22 UTC del 9 y 12 UTC del 10 -> día 10 ; 13 UTC del 10 -> día 11
    assert filas == {"2026-10-10": 3.0, "2026-10-11": 1.0}


def test_comunidad_y_vista_unificada(conn):
    csv = ('"Fecha","Tipo","Escuela","Reportero","Lluvia_mm","Resumen","Instrumento"\n'
           '"10/10/2026 09:30:00","lluvia","San Vicente","Ana","4","Lluvia","Pluviómetro casero"\n')
    reps = comunidad.parsear_csv(csv, {"San Vicente": "san_vicente"})
    assert comunidad.guardar(conn, reps) == 1
    comunidad.guardar(conn, reps)  # sin duplicar
    filas = consulta(conn, "SELECT origen, instrumento, lluvia_mm FROM lluvia_diaria_todas "
                           "WHERE dia = '2026-10-11' ORDER BY origen")
    assert ("comunidad:San Vicente", "pluviometro_casero", 4) in [(a, b, float(c)) for a, b, c in filas]


def test_pronosticos_sin_duplicar(conn):
    p = json.loads((Path(__file__).parent / "datos/pronostico_laplata.json").read_text())
    assert pronosticos.guardar(conn, [p]) == 1
    assert pronosticos.guardar(conn, [p]) == 0
    n = consulta(conn, "SELECT count(*) FROM pronostico_dia")[0][0]
    assert n == len(p["daily_forecasts"])


def test_bitacora(conn):
    with db.registrar_tarea(conn, "prueba") as reg:
        reg["filas"] = 7
    with pytest.raises(ValueError):
        with db.registrar_tarea(conn, "prueba_falla"):
            raise ValueError("ups")
    filas = dict(consulta(conn, "SELECT tarea, ok FROM ingesta_log"))
    assert filas == {"prueba": True, "prueba_falla": False}


def test_lluvia_diaria_sinoptica_usa_24h_de_las_12utc(conn):
    obs = [Obs(datetime(2026, 10, 10, 12, tzinfo=timezone.utc), "omm_87593", "precip_24h", 3.0),
           Obs(datetime(2026, 10, 10, 6, tzinfo=timezone.utc), "omm_87593", "precip_24h", 9.9),   # otro período
           Obs(datetime(2026, 10, 10, 12, tzinfo=timezone.utc), "omm_87593", "precip_6h", 2.0)]   # no se suma
    db.guardar_observaciones(conn, obs, "test")
    filas = consulta(conn, "SELECT dia::text, lluvia_mm::float, instrumento FROM lluvia_diaria_todas "
                           "WHERE origen = 'omm_87593'")
    assert filas == [("2026-10-10", 3.0, "pluviometro_convencional")]
