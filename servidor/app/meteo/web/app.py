"""
API web + visualizador.

Qué es una API: un conjunto de direcciones (URLs) que devuelven DATOS en vez de
páginas. Por ejemplo:

    /api/series?estacion=bavio&estacion=ema_blanca&variable=temp&dias=3

devuelve en JSON la temperatura de esas dos estaciones en los últimos 3 días.
La página (static/index.html) le pide los datos a estas URLs y los dibuja.
Cualquier otro programa (un script de Python, una planilla) puede usar las
mismas URLs: ?formato=csv devuelve un CSV listo para abrir en Excel.

Seguridad:
- Todas las consultas abren la base en modo SOLO LECTURA
  (default_transaction_read_only): aunque hubiera un error en el código, la
  API no puede modificar ni borrar nada.
- Los parámetros nunca se pegan dentro del texto SQL: van aparte (%s), así
  que no hay "inyección SQL" posible.

Arranque (lo hace docker compose):
    uvicorn meteo.web.app:app --host 0.0.0.0 --port 8080
"""
import csv
import io
import math
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ.get("DATABASE_URL", "postgresql://meteo:meteo@db:5432/meteo")
ESTATICOS = Path(__file__).parent / "static"

# Un "pool" mantiene unas pocas conexiones abiertas y las reutiliza: abrir una
# conexión nueva por cada pedido sería lento.
pool = ConnectionPool(
    DSN, min_size=1, max_size=4, open=False,
    kwargs={"options": "-c default_transaction_read_only=on -c statement_timeout=20000",
            "row_factory": dict_row},
)

@asynccontextmanager
async def ciclo_de_vida(_app):
    pool.open()   # al arrancar
    yield
    pool.close()  # al apagar


app = FastAPI(title="Servidor meteorológico", docs_url="/api/docs", redoc_url=None, lifespan=ciclo_de_vida)


def consultar(sql, params=()):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _iso(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _json(filas):
    return [{k: _iso(v) for k, v in f.items()} for f in filas]


def _csv(filas, nombre, campos=None):
    buf = io.StringIO()
    campos = list(filas[0].keys()) if filas else campos
    if campos:
        w = csv.DictWriter(buf, fieldnames=campos)
        w.writeheader()
        for f in filas:
            w.writerow({k: _iso(v) for k, v in f.items()})
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'})


# ---------------------------------------------------------------------------
# Estaciones con su último dato
# ---------------------------------------------------------------------------
VARIABLES_RESUMEN = ("temp", "hum", "td", "pres_est", "pnm", "viento_vel", "viento_dir")


@app.get("/api/estaciones")
def estaciones():
    """Metadatos de cada estación + últimos valores + lluvia desde las 9."""
    est = consultar("""
        SELECT id, nombre, tipo, red, fuente, institucion, localidad, lat, lon,
               elevacion_m, omm, activa, notas
        FROM estacion ORDER BY tipo, nombre""")
    # Último valor de cada variable (mirando solo los últimos 3 días: rápido).
    ultimos = consultar("""
        SELECT DISTINCT ON (estacion_id, variable) estacion_id, variable, ts, valor
        FROM observacion
        WHERE ts > now() - interval '3 days' AND qc <> 4 AND variable = ANY(%s)
        ORDER BY estacion_id, variable, ts DESC""", (list(VARIABLES_RESUMEN),))
    ultimo_ts = consultar("""
        SELECT estacion_id, max(ts) AS ts FROM observacion
        WHERE ts > now() - interval '30 days' GROUP BY 1""")
    # Lluvia del día pluviométrico en curso (desde las 9 hora argentina).
    lluvia = consultar("""
        SELECT estacion_id, round(sum(valor)::numeric, 1)::float AS mm
        FROM observacion
        WHERE variable = 'precip' AND qc <> 4
          AND dia_pluviometrico(ts) = dia_pluviometrico(now())
        GROUP BY 1""")

    por_est = {e["id"]: {**e, "ultimo": None, "valores": {}, "lluvia_hoy": None} for e in est}
    for f in ultimo_ts:
        if f["estacion_id"] in por_est:
            por_est[f["estacion_id"]]["ultimo"] = _iso(f["ts"])
    for f in ultimos:
        if f["estacion_id"] in por_est:
            por_est[f["estacion_id"]]["valores"][f["variable"]] = {"valor": f["valor"], "ts": _iso(f["ts"])}
    for f in lluvia:
        if f["estacion_id"] in por_est:
            por_est[f["estacion_id"]]["lluvia_hoy"] = f["mm"]
    return list(por_est.values())


@app.get("/api/variables")
def variables():
    return consultar("SELECT id, nombre, unidad, descripcion FROM variable ORDER BY id")


# ---------------------------------------------------------------------------
# Series de tiempo
# ---------------------------------------------------------------------------
@app.get("/api/series")
def series(
    variable: str,
    estacion: list[str] = Query(...),
    dias: float = 3,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    paso: str = "auto",
    formato: str = "json",
):
    """Serie de una variable para una o más estaciones.

    paso: crudo | hora | dia | auto (crudo hasta 3 días, horario hasta 60, diario después).
    La lluvia (precip) se SUMA al agregar; el resto se PROMEDIA. Los datos con
    qc = 4 (malos) nunca se incluyen.
    """
    hasta = hasta or datetime.now(timezone.utc)
    desde = desde or hasta - timedelta(days=dias)
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=timezone.utc)
    if hasta.tzinfo is None:
        hasta = hasta.replace(tzinfo=timezone.utc)
    if len(estacion) > 8:
        raise HTTPException(400, "Máximo 8 estaciones por consulta")
    rango = (hasta - desde).total_seconds() / 86400
    if paso == "auto":
        paso = "crudo" if rango <= 3 else ("hora" if rango <= 60 else "dia")
    if paso not in ("crudo", "hora", "dia"):
        raise HTTPException(400, "paso debe ser crudo, hora, dia o auto")

    info = consultar("SELECT id, nombre, unidad FROM variable WHERE id = %s", (variable,))
    if not info:
        raise HTTPException(404, f"Variable desconocida: {variable}")

    agregado = "sum(valor)" if variable.startswith("precip") else "avg(valor)"
    if paso == "crudo":
        sql = """SELECT estacion_id, ts, valor FROM observacion
                 WHERE variable = %s AND estacion_id = ANY(%s) AND ts BETWEEN %s AND %s AND qc <> 4
                 ORDER BY estacion_id, ts"""
    else:
        # Para días se usa el día local argentino (o el pluviométrico, si es lluvia).
        if paso == "hora":
            bucket = "date_trunc('hour', ts)"
        elif variable.startswith("precip"):
            bucket = "(dia_pluviometrico(ts)::timestamp AT TIME ZONE 'America/Argentina/Buenos_Aires')"
        else:
            bucket = ("(date_trunc('day', ts AT TIME ZONE 'America/Argentina/Buenos_Aires') "
                      "AT TIME ZONE 'America/Argentina/Buenos_Aires')")
        sql = f"""SELECT estacion_id, {bucket} AS ts, {agregado} AS valor, count(*) AS n
                  FROM observacion
                  WHERE variable = %s AND estacion_id = ANY(%s) AND ts BETWEEN %s AND %s AND qc <> 4
                  GROUP BY 1, 2 ORDER BY 1, 2"""
    filas = consultar(sql, (variable, estacion, desde, hasta))

    if formato == "csv":
        campos = ["estacion_id", "ts", "valor"] + ([] if paso == "crudo" else ["n"])
        return _csv(filas, f"{variable}_{desde:%Y%m%d}_{hasta:%Y%m%d}", campos)
    out = {e: [] for e in estacion}
    for f in filas:
        out[f["estacion_id"]].append([_iso(f["ts"]), round(f["valor"], 3)])
    return {"variable": info[0], "paso": paso, "desde": _iso(desde), "hasta": _iso(hasta),
            "series": [{"estacion": e, "puntos": p} for e, p in out.items()]}


# ---------------------------------------------------------------------------
# Lluvia diaria (9 a 9), todas las fuentes
# ---------------------------------------------------------------------------
@app.get("/api/lluvia")
def lluvia(dias: int = 14, formato: str = "json"):
    dias = max(1, min(dias, 366))
    filas = consultar("""
        SELECT dia, origen, nombre, tipo_origen, instrumento, lluvia_mm::float AS lluvia_mm,
               mm_sospechosos::float AS mm_sospechosos
        FROM lluvia_diaria_todas
        WHERE dia > dia_pluviometrico(now()) - %s
        ORDER BY dia, origen""", (dias,))
    if formato == "csv":
        return _csv(filas, f"lluvia_diaria_{dias}d")
    return _json(filas)


# ---------------------------------------------------------------------------
# Estado de la ingesta
# ---------------------------------------------------------------------------
@app.get("/api/estado")
def estado():
    tareas = consultar("""
        SELECT DISTINCT ON (tarea) tarea, inicio, fin, ok, filas, mensaje
        FROM ingesta_log WHERE inicio > now() - interval '7 days'
        ORDER BY tarea, inicio DESC""")
    fallas = consultar("""
        SELECT tarea, count(*) AS fallas FROM ingesta_log
        WHERE NOT ok AND inicio > now() - interval '24 hours' GROUP BY 1""")
    n = {f["tarea"]: f["fallas"] for f in fallas}
    total = consultar("SELECT count(*) AS n, min(ts) AS desde FROM observacion")[0]
    return {"tareas": [{**t, "fallas_24h": n.get(t["tarea"], 0)} for t in _json(tareas)],
            "observaciones": total["n"], "desde": _iso(total["desde"])}


@app.get("/api/reportes")
def reportes(dias: int = 30):
    return _json(consultar("""
        SELECT ts, tipo, escuela, reportero, lluvia_mm, instrumento, periodo, estacion_id,
               resumen, imagen_url, lat, lon
        FROM reporte_comunidad WHERE ts > now() - make_interval(days => %s)
        ORDER BY ts DESC""", (max(1, min(dias, 3660)),)))


# ---------------------------------------------------------------------------
# EMAs: meteograma y resumen de la red
# ---------------------------------------------------------------------------
TZ_AR = "America/Argentina/Buenos_Aires"
VARIABLES_METEOGRAMA = ["temp", "td", "hum", "pnm", "pres_est", "precip", "viento_vel",
                        "racha", "viento_dir", "rad_solar"]


@app.get("/api/estacion/{estacion_id}/meteograma")
def meteograma(estacion_id: str, dias: float = 2):
    """Todas las variables de UNA estación, listas para paneles apilados.

    Hasta 3 días: cada dato tal cual llegó; más: promedios horarios. La lluvia
    va siempre en sumas horarias (barras), que es como se lee una EMA."""
    dias = max(0.25, min(dias, 60))
    hasta = datetime.now(timezone.utc)
    desde = hasta - timedelta(days=dias)
    crudo = dias <= 3
    otras = [v for v in VARIABLES_METEOGRAMA if v != "precip"]
    if crudo:
        filas = consultar("""
            SELECT variable, ts, valor FROM observacion
            WHERE estacion_id = %s AND variable = ANY(%s) AND ts BETWEEN %s AND %s AND qc <> 4
            ORDER BY ts""", (estacion_id, otras, desde, hasta))
    else:
        filas = consultar("""
            SELECT variable, date_trunc('hour', ts) AS ts, avg(valor) AS valor FROM observacion
            WHERE estacion_id = %s AND variable = ANY(%s) AND ts BETWEEN %s AND %s AND qc <> 4
            GROUP BY 1, 2 ORDER BY 2""", (estacion_id, otras, desde, hasta))
    filas += consultar("""
        SELECT 'precip' AS variable, date_trunc('hour', ts) + interval '1 hour' AS ts, sum(valor) AS valor
        FROM observacion
        WHERE estacion_id = %s AND variable = 'precip' AND ts > %s AND ts <= %s AND qc <> 4
        GROUP BY 2 ORDER BY 2""", (estacion_id, desde, hasta))
    series = {}
    for f in filas:
        series.setdefault(f["variable"], []).append([_iso(f["ts"]), round(f["valor"], 2)])
    return {"estacion": estacion_id, "paso": "crudo" if crudo else "hora",
            "desde": _iso(desde), "hasta": _iso(hasta), "series": series}


@app.get("/api/emas/resumen")
def resumen_emas():
    """Hoy en cada EMA: último dato, máxima y mínima del día civil y lluvia
    desde las 9 (día pluviométrico en curso)."""
    return _json(consultar(f"""
        WITH hoy AS (
            SELECT (date_trunc('day', now() AT TIME ZONE '{TZ_AR}') AT TIME ZONE '{TZ_AR}') AS inicio
        )
        SELECT e.id, e.nombre, e.localidad,
               max(o.ts) AS ultimo,
               (array_agg(o.valor ORDER BY o.ts DESC) FILTER (WHERE o.variable = 'temp'))[1] AS temp,
               (array_agg(o.valor ORDER BY o.ts DESC) FILTER (WHERE o.variable = 'hum'))[1] AS hum,
               max(o.valor) FILTER (WHERE o.variable = 'temp' AND o.ts >= hoy.inicio) AS tmax_hoy,
               min(o.valor) FILTER (WHERE o.variable = 'temp' AND o.ts >= hoy.inicio) AS tmin_hoy,
               round(sum(o.valor) FILTER (WHERE o.variable = 'precip'
                       AND dia_pluviometrico(o.ts) = dia_pluviometrico(now()))::numeric, 1)::float AS lluvia_desde_9
        FROM estacion e
        CROSS JOIN hoy
        LEFT JOIN observacion o ON o.estacion_id = e.id AND o.qc <> 4 AND o.ts > now() - interval '30 hours'
        WHERE e.tipo = 'ema' AND e.activa
        GROUP BY e.id, e.nombre, e.localidad
        ORDER BY e.nombre"""))


# ---------------------------------------------------------------------------
# Estaciones de superficie (SMN): tabla de observaciones y resumen diario
# ---------------------------------------------------------------------------
def humedad_relativa(t, td):
    """HR (%) a partir de temperatura y rocío (fórmula de Magnus)."""
    if t is None or td is None:
        return None
    return round(100 * math.exp(17.625 * td / (243.04 + td)) / math.exp(17.625 * t / (243.04 + t)))


COLUMNAS_SYNOP = ["temp", "td", "hum", "pnm", "pres_est", "viento_dir", "viento_vel", "nubosidad",
                  "visibilidad", "ww", "precip_1h", "precip_3h", "precip_6h", "precip_12h",
                  "precip_24h", "tmax", "tmin"]


@app.get("/api/sinoptica/{estacion_id}/observaciones")
def observaciones_sinoptica(estacion_id: str, dias: float = 2, formato: str = "json"):
    """Una fila por mensaje SYNOP (hora), con todas sus variables, como el
    parte de observaciones del SMN. La humedad se calcula de T y Td si el
    mensaje no la trae."""
    dias = max(0.25, min(dias, 31))
    columnas = ",\n".join(f"max(valor) FILTER (WHERE variable = '{c}') AS {c}" for c in COLUMNAS_SYNOP)
    filas = consultar(f"""
        SELECT ts, {columnas}
        FROM observacion
        WHERE estacion_id = %s AND ts > now() - make_interval(secs => %s) AND qc <> 4
        GROUP BY ts ORDER BY ts DESC""", (estacion_id, dias * 86400))
    for f in filas:
        if f["hum"] is None:
            f["hum"] = humedad_relativa(f["temp"], f["td"])
    crudos = {r["ts"]: r["mensaje"] for r in consultar(
        "SELECT ts, mensaje FROM synop_crudo WHERE estacion_id = %s AND ts > now() - make_interval(secs => %s)",
        (estacion_id, dias * 86400))}
    for f in filas:
        f["mensaje"] = crudos.get(f["ts"])
    if formato == "csv":
        return _csv(filas, f"{estacion_id}_observaciones", ["ts"] + COLUMNAS_SYNOP + ["mensaje"])
    return _json(filas)


@app.get("/api/sinopticas/resumen")
def resumen_sinopticas(dias: int = 3):
    """Por estación y día (hora argentina): máxima y mínima de las
    observaciones horarias, y lluvia de 24 h informada a las 9."""
    dias = max(1, min(dias, 31))
    return _json(consultar(f"""
        WITH temps AS (
            SELECT o.estacion_id, (o.ts AT TIME ZONE '{TZ_AR}')::date AS dia,
                   max(o.valor) AS tmax, min(o.valor) AS tmin, count(*) AS n
            FROM observacion o JOIN estacion e ON e.id = o.estacion_id
            WHERE e.tipo = 'sinoptica' AND o.variable = 'temp' AND o.qc <> 4
              AND o.ts > now() - make_interval(days => %s + 1)
            GROUP BY 1, 2
        )
        SELECT e.id, e.nombre, t.dia, t.tmax, t.tmin, t.n AS observaciones, l.lluvia_mm::float AS lluvia_24h_9hs
        FROM temps t
        JOIN estacion e ON e.id = t.estacion_id
        LEFT JOIN lluvia_diaria l ON l.estacion_id = t.estacion_id AND l.dia = t.dia
        WHERE t.dia > (now() AT TIME ZONE '{TZ_AR}')::date - %s
        ORDER BY e.nombre, t.dia""", (dias, dias)))


@app.exception_handler(Exception)
def _error(_request, exc):
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# La página y sus archivos. Va al final para que /api/... tenga prioridad.
app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="web")
