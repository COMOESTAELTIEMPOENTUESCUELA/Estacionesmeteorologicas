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
        SELECT dia, origen, nombre, tipo_origen, instrumento, lluvia_mm::float AS lluvia_mm
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


@app.exception_handler(Exception)
def _error(_request, exc):
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# La página y sus archivos. Va al final para que /api/... tenga prioridad.
app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="web")
