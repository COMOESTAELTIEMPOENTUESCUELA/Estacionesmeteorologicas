"""
Todo lo que habla con PostgreSQL.

La regla de oro: la ingesta es IDEMPOTENTE. Se puede volver a traer el mismo
período mil veces y la base queda igual, porque cada (estación, variable,
instante) es única y se usa `INSERT ... ON CONFLICT DO UPDATE` ("upsert").
Eso permite pedir siempre "las últimas 6 horas" sin preocuparse por duplicar.
"""
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg

log = logging.getLogger(__name__)


def conectar(dsn=None):
    dsn = dsn or os.environ.get("DATABASE_URL", "postgresql://meteo:meteo@db:5432/meteo")
    return psycopg.connect(dsn, autocommit=False)


# ---------------------------------------------------------------------------
# Control de calidad básico: límites físicos de cada variable (tabla variable)
# ---------------------------------------------------------------------------
def limites(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, min_fisico, max_fisico FROM variable")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def control_rango(obs, lim):
    """Devuelve la bandera qc: 4 si está fuera de los límites físicos, 1 si
    pasó. Si la observación ya traía qc (ej. 4 = descartada por el
    observador), se respeta."""
    if obs.qc:
        return obs.qc
    mn, mx = lim.get(obs.variable, (None, None))
    if (mn is not None and obs.valor < mn) or (mx is not None and obs.valor > mx):
        return 4
    return 1


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------
SQL_UPSERT = """
INSERT INTO observacion (ts, estacion_id, variable, valor, qc, fuente)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (estacion_id, variable, ts) DO UPDATE
   SET valor = EXCLUDED.valor, qc = EXCLUDED.qc, fuente = EXCLUDED.fuente,
       ingestado = now()
 WHERE observacion.valor IS DISTINCT FROM EXCLUDED.valor
    OR observacion.qc IS DISTINCT FROM EXCLUDED.qc
"""


def guardar_observaciones(conn, observaciones, fuente):
    """Guarda una lista de Obs. Devuelve cuántas filas se insertaron o cambiaron."""
    if not observaciones:
        return 0
    lim = limites(conn)
    validas = set(lim)
    filas = []
    for o in observaciones:
        if o.variable not in validas:
            log.warning("Variable desconocida %r (estación %s): se ignora", o.variable, o.estacion_id)
            continue
        filas.append((o.ts, o.estacion_id, o.variable, o.valor, control_rango(o, lim), fuente))
    with conn.cursor() as cur:
        cur.executemany(SQL_UPSERT, filas)
        n = cur.rowcount
    conn.commit()
    return max(n, 0)


def guardar_synop_crudo(conn, mensajes):
    """mensajes: lista de (estacion_id, ts, texto)."""
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO synop_crudo (estacion_id, ts, mensaje) VALUES (%s, %s, %s)
               ON CONFLICT (estacion_id, ts) DO UPDATE SET mensaje = EXCLUDED.mensaje""",
            mensajes,
        )
    conn.commit()


def sincronizar_estaciones(conn, estaciones):
    """Copia los metadatos de estaciones.yaml a la tabla estacion."""
    with conn.cursor() as cur:
        for e in estaciones:
            cur.execute(
                """INSERT INTO estacion (id, nombre, tipo, red, fuente, institucion, localidad,
                                         lat, lon, elevacion_m, omm, activa, notas, config)
                   VALUES (%(id)s, %(nombre)s, %(tipo)s, %(red)s, %(fuente)s, %(institucion)s,
                           %(localidad)s, %(lat)s, %(lon)s, %(elevacion_m)s, %(omm)s, %(activa)s,
                           %(notas)s, %(config)s)
                   ON CONFLICT (id) DO UPDATE SET
                     nombre = EXCLUDED.nombre, tipo = EXCLUDED.tipo, red = EXCLUDED.red,
                     fuente = EXCLUDED.fuente, institucion = EXCLUDED.institucion,
                     localidad = EXCLUDED.localidad, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                     elevacion_m = EXCLUDED.elevacion_m, omm = EXCLUDED.omm,
                     activa = EXCLUDED.activa, notas = EXCLUDED.notas, config = EXCLUDED.config""",
                {
                    "id": e["id"], "nombre": e["nombre"], "tipo": e["tipo"], "red": e.get("red"),
                    "fuente": e["fuente"], "institucion": e.get("institucion"),
                    "localidad": e.get("localidad"), "lat": e.get("lat"), "lon": e.get("lon"),
                    "elevacion_m": e.get("elevacion_m"), "omm": e.get("omm"),
                    "activa": e.get("activa", True), "notas": e.get("notas"),
                    # Las claves de API no se copian a la base: no hacen falta para analizar.
                    "config": json.dumps({k: v for k, v in (e.get("config") or {}).items()
                                          if k not in ("canales", "wu_key")}),
                },
            )
            # Instrumentos declarados en el YAML (solo se agregan si no existen).
            for ins in e.get("instrumentos", []):
                cur.execute(
                    """INSERT INTO instrumento (estacion_id, variable, tipo, marca_modelo, resolucion, notas)
                       SELECT %s, %s, %s, %s, %s, %s
                       WHERE NOT EXISTS (SELECT 1 FROM instrumento
                                         WHERE estacion_id = %s AND variable IS NOT DISTINCT FROM %s
                                           AND tipo = %s AND hasta IS NULL)""",
                    (e["id"], ins.get("variable"), ins["tipo"], ins.get("marca_modelo"),
                     ins.get("resolucion"), ins.get("notas"), e["id"], ins.get("variable"), ins["tipo"]),
                )
    conn.commit()


# ---------------------------------------------------------------------------
# Bitácora
# ---------------------------------------------------------------------------
@contextmanager
def registrar_tarea(conn, tarea):
    """Uso:  with registrar_tarea(conn, "thingspeak") as reg: ... reg["filas"] = n"""
    inicio = datetime.now(timezone.utc)
    reg = {"filas": 0, "mensaje": None}
    ok = False
    try:
        yield reg
        ok = True
    except Exception as e:
        conn.rollback()
        reg["mensaje"] = f"{type(e).__name__}: {e}"[:2000]
        raise
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ingesta_log (tarea, inicio, fin, ok, filas, mensaje) VALUES (%s,%s,%s,%s,%s,%s)",
                    (tarea, inicio, datetime.now(timezone.utc), ok, reg["filas"], reg["mensaje"]),
                )
            conn.commit()
        except Exception:
            log.exception("No se pudo escribir la bitácora")
            conn.rollback()
