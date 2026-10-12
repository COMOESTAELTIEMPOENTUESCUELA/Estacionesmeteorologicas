"""
Tests contra una base PostgreSQL real. Se saltean si no hay una disponible.
Para correrlos: TEST_DATABASE_URL=postgresql://... pytest
(la base tiene que estar VACÍA: el test carga el esquema).
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
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


def test_lluvia_sospechosa_se_suma_pero_se_informa(conn):
    t = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)  # 06 hora argentina
    db.guardar_observaciones(conn, [Obs(t, "ep23_los_talas", "precip", 0.25, 3),
                                    Obs(t + timedelta(minutes=5), "ep23_los_talas", "precip", 1.0)], "test")
    [(mm, sosp)] = consulta(conn, "SELECT lluvia_mm::float, mm_sospechosos::float FROM lluvia_diaria_todas "
                                  "WHERE origen = 'ep23_los_talas' AND dia = '2026-10-10'")
    assert (mm, sosp) == (1.3, 0.3)  # Postgres redondea 1,25 -> 1,3 y 0,25 -> 0,3


def test_reemplazar_borra_restos_de_calculos_viejos(conn):
    from meteo.ingesta.fuentes import wunderground
    t = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)   # 15 hora argentina
    # Cálculo viejo: una fila inflada en un minuto que la fuente ya no devuelve.
    db.guardar_observaciones(conn, [Obs(t + timedelta(minutes=2), "ep23_los_talas", "precip", 40.0)], "test")
    nuevas = [Obs(t, "ep23_los_talas", "precip", 0.5), Obs(t + timedelta(minutes=5), "ep23_los_talas", "precip", 0.0)]
    db.guardar_observaciones(conn, nuevas, "test", wunderground.dias_a_reemplazar("ep23_los_talas", nuevas))
    [(n, total)] = consulta(conn, "SELECT count(*), sum(valor) FROM observacion WHERE estacion_id = 'ep23_los_talas' "
                                  "AND variable = 'precip' AND ts::date = '2026-10-07'")
    assert (n, total) == (2, 0.5)
    # La temperatura del mismo día no se toca.
    db.guardar_observaciones(conn, [Obs(t, "ep23_los_talas", "temp", 20.0)], "test")
    db.guardar_observaciones(conn, nuevas, "test", wunderground.dias_a_reemplazar("ep23_los_talas", nuevas))
    assert consulta(conn, "SELECT count(*) FROM observacion WHERE estacion_id = 'ep23_los_talas' "
                          "AND variable = 'temp' AND ts::date = '2026-10-07'")[0][0] == 1


def test_conexion_con_limites_de_espera(conn):
    assert consulta(conn, "SHOW lock_timeout")[0][0] == "30s"
    assert consulta(conn, "SHOW statement_timeout")[0][0] == "5min"


def test_reconecta_si_se_corta_la_conexion():
    from meteo.ingesta.tareas import Ingesta
    ing = Ingesta({"estaciones": []}, db.conectar(DSN))
    ing.conn.close()                       # simula que se reinició la base
    assert not db.conexion_sana(ing.conn)
    import os
    os.environ["DATABASE_URL"] = DSN
    ing.asegurar_conexion()
    assert db.conexion_sana(ing.conn)
    ing.conn.close()


def test_verificacion_de_punta_a_punta(conn):
    """SYNOP horarios de Tandil -> lo observado del día -> pares con el pronóstico."""
    from meteo.verificacion import observado

    ar = timezone(timedelta(hours=-3))
    obs = []
    for h in range(0, 24 * 3):           # 08/10 a 10/10, hora argentina
        t = datetime(2026, 10, 8, tzinfo=ar) + timedelta(hours=h)
        hora = h % 24
        obs += [Obs(t, "omm_87645", "temp", 10 + 8 * (1 - abs(hora - 15) / 15)),
                Obs(t, "omm_87645", "viento_vel", 5.0), Obs(t, "omm_87645", "viento_dir", 225)]
    obs.append(Obs(datetime(2026, 10, 10, 15, tzinfo=ar), "omm_87645", "ww", 61))
    db.guardar_observaciones(conn, obs, "test")
    emision = {"city": "Tandil", "forecast_date": "2026-10-09", "emission_time": "08:00", "publicado_por": "Ana",
               "daily_forecasts": [{"date": "2026-10-10", "temp_min": 11, "temp_max": 19, "period_forecasts": [
                   {"period": "tarde", "precipitation_description": "lluvias", "wind_direction": "sudoeste",
                    "wind_intensity": "leves a moderados"}]}]}
    pronosticos.guardar(conn, [emision])

    cfg = cargar_config(RAIZ / "config/estaciones.yaml")["verificacion"]
    # De a tramos de 1 día tiene que dar lo mismo que de una vez.
    observado.DIAS_POR_TRAMO = 1
    n1 = observado.calcular(conn, cfg, date(2026, 10, 9), date(2026, 10, 10))
    observado.DIAS_POR_TRAMO = 31
    assert observado.calcular(conn, cfg, date(2026, 10, 9), date(2026, 10, 10)) == n1 > 0
    filas = {(f.isoformat(), el): (v, c) for f, el, v, c in consulta(
        conn, "SELECT fecha, elemento, valor, categoria FROM verif_observado WHERE ciudad = 'Tandil'")}
    assert filas[("2026-10-10", "tmax_urbana")] == (18.0, None)
    assert filas[("2026-10-10", "pp")] == (1.0, "SI")
    assert filas[("2026-10-09", "pp")] == (0.0, "NO")
    assert filas[("2026-10-10", "viento_dir")] == (None, "SW")
    assert filas[("2026-10-10", "viento_int")] == (18.0, "LEVES")


def test_importar_hoja_sin_duplicar_ni_pisar_lo_capturado(conn):
    from meteo.ingesta.fuentes import hoja_verificacion
    texto = (Path(__file__).parent / "datos/hoja_verificacion.csv").read_text(encoding="utf-8")
    # La Plata 11/10 ya la capturó el servidor en vivo: no se importa.
    pronosticos.guardar(conn, [{"city": "La Plata", "forecast_date": "2026-10-11", "daily_forecasts": []}])
    assert hoja_verificacion.importar(conn, texto) == (2, 1)
    assert hoja_verificacion.importar(conn, texto) == (0, 1)   # repetir no duplica
    filas = consulta(conn, """SELECT d.fecha::text, d.plazo_dias, d.temp_max FROM pronostico_dia d
                              JOIN pronostico_emision e ON e.id = d.emision_id
                              WHERE e.contenido ->> 'origen' IS NOT NULL ORDER BY 1, 2""")
    assert ("2026-08-15", 1, 13.0) in filas and ("2026-08-15", 3, 15.0) in filas and len(filas) == 4


def test_correccion_de_fecha_de_un_pronostico(conn):
    """Publicado el viernes con "para mañana" tildado: se corre un día en el
    lugar, una sola vez, y si vuelve a llegar el original no se duplica."""
    from meteo.ingesta import correcciones
    from meteo.ingesta.fuentes import pronosticos
    p = {"city": "Ciudad Test", "forecast_date": "2026-10-10", "emission_time": "08:00:00", "publicado_por": "Micaela",
         "daily_forecasts": [{"date": "2026-10-10", "day_name": "sábado", "temp_min": 11, "temp_max": 16},
                             {"date": "2026-10-11", "day_name": "domingo", "temp_min": 11, "temp_max": 16}]}
    assert pronosticos.guardar(conn, [p]) == 1
    c = [{"ciudad": "Ciudad Test", "fecha_guardada": "2026-10-10", "publicado_por": "Micaela",
          "hora_emision": "08:00:00", "mover_dias": -1, "publicado_el": "2026-10-09", "motivo": "test"}]
    try:
        assert correcciones.aplicar(conn, c) == ["Ciudad Test 2026-10-10 (Micaela): corregido a 2026-10-09"]
        assert correcciones.aplicar(conn, c) == ["Ciudad Test 2026-10-10 (Micaela): ya estaba corregido"]
        assert pronosticos.guardar(conn, [p]) == 0                 # el original equivocado no vuelve a entrar
        with conn.cursor() as cur:
            cur.execute("""SELECT e.fecha_pronostico::text, d.fecha::text, d.plazo_dias, e.contenido -> 'publicado_el' ->> 0
                           FROM pronostico_dia d JOIN pronostico_emision e ON e.id = d.emision_id
                           WHERE e.ciudad = 'Ciudad Test' ORDER BY d.fecha""")
            assert cur.fetchall() == [("2026-10-09", "2026-10-09", 0, "2026-10-09"), ("2026-10-09", "2026-10-10", 1, "2026-10-09")]
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pronostico_emision WHERE ciudad = 'Ciudad Test'")
        conn.commit()
