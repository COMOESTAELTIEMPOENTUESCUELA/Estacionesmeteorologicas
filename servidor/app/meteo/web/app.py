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
import asyncio
import base64
import csv
import io
import logging
import math
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ..verificacion import puntajes
from ..verificacion.etiquetas import ETIQUETAS, UMBRALES

DSN = os.environ.get("DATABASE_URL", "postgresql://meteo:meteo@db:5432/meteo")
ESTATICOS = Path(__file__).parent / "static"

# Un "pool" mantiene unas pocas conexiones abiertas y las reutiliza: abrir una
# conexión nueva por cada pedido sería lento.
pool = ConnectionPool(
    DSN, min_size=1, max_size=4, open=False,
    kwargs={"options": "-c default_transaction_read_only=on -c statement_timeout=20000",
            "row_factory": dict_row},
)

# Lo ÚNICO que la web escribe: los dibujos de análisis sobre las cartas y la
# bitácora de las estaciones (fallas y mantenimiento). Va por una conexión
# aparte, para que todo lo demás siga en solo lectura.
# En la copia pública (modo cartas) no existe: ahí no se escribe nada.
pool_escritura = None if os.environ.get("WEB_MODO", "completo") == "cartas" else ConnectionPool(
    DSN, min_size=0, max_size=2, open=False,
    kwargs={"options": "-c statement_timeout=10000", "row_factory": dict_row},
)
# Tablas que crea la propia web al arrancar (si no existen).
TABLAS_PROPIAS = [Path(__file__).parent / "dibujos.sql", Path(__file__).parent / "incidentes.sql"]


@asynccontextmanager
async def ciclo_de_vida(_app):
    pool.open()   # al arrancar
    if pool_escritura:
        pool_escritura.open()
        for sql in TABLAS_PROPIAS:
            try:
                with pool_escritura.connection() as conn:
                    conn.execute(sql.read_text(encoding="utf-8"))
            except Exception as ex:      # sin la tabla, la web anda igual (sin esa parte)
                logging.getLogger(__name__).warning("tabla de %s: %s", sql.stem, ex)
    yield
    pool.close()  # al apagar
    if pool_escritura:
        pool_escritura.close()


app = FastAPI(title="Servidor meteorológico", docs_url="/api/docs", redoc_url=None, lifespan=ciclo_de_vida)


# ---------------------------------------------------------------------------
# Contraseña (opcional)
# ---------------------------------------------------------------------------
# Si en .env están WEB_USUARIO y WEB_CLAVE, toda la página y la API piden
# usuario y contraseña ("autenticación básica" de HTTP: el navegador muestra
# una ventanita y la recuerda). Si no están, la página queda abierta como
# antes. Ojo: viaja cifrada solo con HTTPS (Tailscale Funnel lo tiene); por
# http:// en la red de la casa viaja legible, pero ahí no sale de la casa.
WEB_USUARIO = os.environ.get("WEB_USUARIO", "")
WEB_CLAVE = os.environ.get("WEB_CLAVE", "")


def _usuario(cabecera):
    """El usuario con que se entró (para anotar quién dibujó), o None."""
    if not cabecera.startswith("Basic "):
        return None
    try:
        return base64.b64decode(cabecera[6:]).decode("utf-8").partition(":")[0] or None
    except Exception:
        return None


def credenciales_validas(cabecera):
    if not cabecera.startswith("Basic "):
        return False
    try:
        usuario, _, clave = base64.b64decode(cabecera[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    # compare_digest tarda lo mismo acierte o no: no deja adivinar la clave
    # midiendo cuánto tarda la respuesta.
    return (secrets.compare_digest(usuario.encode(), WEB_USUARIO.encode())
            & secrets.compare_digest(clave.encode(), WEB_CLAVE.encode()))


# Modo "cartas": una copia del visualizador que solo muestra la pestaña
# Cartas, para compartirla hacia afuera (Tailscale Funnel) sin exponer el
# resto. El límite lo pone el SERVIDOR (lista de lo permitido), no la
# página: aunque alguien escriba a mano otra dirección, no la obtiene.
WEB_MODO = os.environ.get("WEB_MODO", "completo")
PERMITIDO_EN_CARTAS = ("/api/modo", "/api/cartas", "/api/verificacion/dia", "/cartas-img/", "/vendor/")


def permitido(ruta):
    if WEB_MODO != "cartas":
        return True
    return ruta in ("/", "/index.html") or ruta.startswith(PERMITIDO_EN_CARTAS)


@app.middleware("http")
async def guardar_cartas_en_el_navegador(request, siguiente):
    """Una carta archivada no cambia nunca: el navegador la puede guardar y no
    volver a pedirla (sin esto, pregunta cada vez si cambió). El fondo de
    Ogimet, un día, por si alguna vez se reemplaza."""
    r = await siguiente(request)
    ruta = request.url.path
    if ruta.startswith("/cartas-img/") and r.status_code == 200:
        r.headers["Cache-Control"] = ("public, max-age=86400" if ruta.startswith("/cartas-img/_fondos/")
                                      else "public, max-age=31536000, immutable")
    return r


@app.middleware("http")
async def solo_lo_permitido(request, siguiente):
    if not permitido(request.url.path):
        return Response("No disponible.", status_code=404)
    if WEB_MODO == "cartas" and request.method not in ("GET", "HEAD"):
        return Response("La copia pública es solo para mirar.", status_code=405)
    return await siguiente(request)


@app.middleware("http")
async def pedir_clave(request, siguiente):
    if WEB_CLAVE and not credenciales_validas(request.headers.get("authorization", "")):
        await asyncio.sleep(1)   # frena a quien prueba claves de a miles
        return Response("Hace falta usuario y contraseña.", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Servidor meteorologico", charset="UTF-8"'})
    return await siguiente(request)


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
# La dirección del viento es un ÁNGULO: el promedio común de 350° y 10° da
# 180° (¡sur!) cuando los dos son vientos casi del norte. Promedio circular:
# se promedian seno y coseno y se vuelve a calcular el ángulo.
PROMEDIO_DIRECCION = "mod((degrees(atan2(avg(sin(radians(valor))), avg(cos(radians(valor))))) + 360)::numeric, 360)"

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

    if variable.startswith("precip"):
        agregado = "sum(valor)"
    elif variable == "viento_dir":
        agregado = PROMEDIO_DIRECCION
    else:
        agregado = "avg(valor)"
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
def lluvia(dias: int = 14, desde: date | None = None, hasta: date | None = None, formato: str = "json"):
    """Lluvia de cada día pluviométrico (9 a 9) de cada origen: los últimos
    `dias`, o de `desde` a `hasta` (días pluviométricos, ambos incluidos)."""
    if desde or hasta:
        hasta = hasta or date.today()
        desde = desde or hasta - timedelta(days=13)
        if desde > hasta:
            desde, hasta = hasta, desde
        if (hasta - desde).days > 366:
            raise HTTPException(400, "366 días como máximo")
        filtro, params, nombre = "dia BETWEEN %s AND %s", (desde, hasta), f"lluvia_diaria_{desde}_{hasta}"
    else:
        dias = max(1, min(dias, 366))
        filtro, params, nombre = "dia > dia_pluviometrico(now()) - %s", (dias,), f"lluvia_diaria_{dias}d"
    filas = consultar(f"""
        SELECT dia, origen, nombre, tipo_origen, instrumento, lluvia_mm::float AS lluvia_mm,
               mm_sospechosos::float AS mm_sospechosos
        FROM lluvia_diaria_todas
        WHERE {filtro}
        ORDER BY dia, origen""", params)
    if formato == "csv":
        return _csv(filas, nombre)
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


def _rango(dias, desde, hasta, max_dias):
    """El período pedido: "los últimos `dias`" o, si vienen, de `desde` a
    `hasta` (para ver un día o un evento del pasado). Sin zona horaria se
    toma como UTC; la página manda las horas argentinas con su -03:00."""
    utc = lambda t: (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if desde is None and hasta is None:
        dias = max(0.25, min(dias, max_dias))
        hasta = datetime.now(timezone.utc)
        return hasta - timedelta(days=dias), hasta
    hasta = utc(hasta) if hasta else datetime.now(timezone.utc)
    desde = utc(desde) if desde else hasta - timedelta(days=dias)
    if desde >= hasta:
        raise HTTPException(400, "desde tiene que ser anterior a hasta")
    if hasta - desde > timedelta(days=max_dias):
        raise HTTPException(400, f"período de {max_dias} días como máximo")
    return desde, hasta


@app.get("/api/estacion/{estacion_id}/meteograma")
def meteograma(estacion_id: str, dias: float = 2, desde: datetime | None = None, hasta: datetime | None = None):
    """Todas las variables de UNA estación, listas para paneles apilados.

    Hasta 3 días: cada dato tal cual llegó; más: promedios horarios. La lluvia
    va siempre en sumas horarias (barras), que es como se lee una EMA."""
    desde, hasta = _rango(dias, desde, hasta, 60)
    crudo = hasta - desde <= timedelta(days=3)
    otras = [v for v in VARIABLES_METEOGRAMA if v != "precip"]
    if crudo:
        filas = consultar("""
            SELECT variable, ts, valor FROM observacion
            WHERE estacion_id = %s AND variable = ANY(%s) AND ts BETWEEN %s AND %s AND qc <> 4
            ORDER BY ts""", (estacion_id, otras, desde, hasta))
    else:
        filas = consultar(f"""
            SELECT variable, date_trunc('hour', ts) AS ts,
                   CASE WHEN variable = 'viento_dir' THEN {PROMEDIO_DIRECCION} ELSE avg(valor) END AS valor
            FROM observacion
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
    """Hoy en cada EMA: último dato, máxima y mínima del día civil, lluvia
    desde las 9 (día pluviométrico en curso) y, si hay, lo anotado en la
    bitácora que está pasando ahora (ej. "corte de internet")."""
    filas = _json(consultar(f"""
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
    abiertos = _incidentes_abiertos()
    for f in filas:
        f["incidente"] = abiertos.get(f["id"]) or abiertos.get(None)
    return filas


def _incidentes_abiertos():
    """Lo anotado en la bitácora que está pasando ahora: {estacion_id: {...}}."""
    try:
        filas = consultar("""
            SELECT DISTINCT ON (estacion_id) id, estacion_id, causa, desde, hasta, descripcion
            FROM estacion_incidente WHERE desde <= now() AND coalesce(hasta, 'infinity') > now()
            ORDER BY estacion_id, desde DESC""")
    except Exception:          # si la tabla todavía no existe
        return {}
    return {f["estacion_id"]: {k: _iso(v) for k, v in f.items()} for f in filas}


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
def observaciones_sinoptica(estacion_id: str, dias: float = 2, desde: datetime | None = None,
                            hasta: datetime | None = None, formato: str = "json"):
    """Una fila por mensaje SYNOP (hora), con todas sus variables, como el
    parte de observaciones del SMN. La humedad se calcula de T y Td si el
    mensaje no la trae. Período: los últimos `dias`, o de `desde` a `hasta`."""
    desde, hasta = _rango(dias, desde, hasta, 31)
    columnas = ",\n".join(f"max(valor) FILTER (WHERE variable = '{c}') AS {c}" for c in COLUMNAS_SYNOP)
    filas = consultar(f"""
        SELECT ts, {columnas}
        FROM observacion
        WHERE estacion_id = %s AND ts > %s AND ts <= %s AND qc <> 4
        GROUP BY ts ORDER BY ts DESC""", (estacion_id, desde, hasta))
    for f in filas:
        if f["hum"] is None:
            f["hum"] = humedad_relativa(f["temp"], f["td"])
    crudos = {r["ts"]: r["mensaje"] for r in consultar(
        "SELECT ts, mensaje FROM synop_crudo WHERE estacion_id = %s AND ts > %s AND ts <= %s",
        (estacion_id, desde, hasta))}
    for f in filas:
        f["mensaje"] = crudos.get(f["ts"])
    if formato == "csv":
        return _csv(filas, f"{estacion_id}_observaciones_{desde:%Y%m%d%H}_{hasta:%Y%m%d%H}",
                    ["ts"] + COLUMNAS_SYNOP + ["mensaje"])
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


# ---------------------------------------------------------------------------
# Verificación de pronósticos (Pronóstico UNLP)
# ---------------------------------------------------------------------------
@app.get("/api/verificacion/ciudades")
def verificacion_ciudades():
    # Solo las ciudades que el grupo pronostica (o pronosticó): una ciudad
    # con observaciones pero sin pronósticos no tiene nada que verificar.
    return [r["ciudad"] for r in consultar("SELECT DISTINCT ciudad FROM pronostico_emision ORDER BY 1")]


@app.get("/api/verificacion")
def verificacion(ciudad: str = "La Plata", desde: date | None = None, hasta: date | None = None,
                 version: str = "final", publicacion: str = "todas", etiqueta: str = "",
                 formato: str = "json"):
    """Pronóstico vs. observado, día por día, y las medidas de error por
    plazo. version=final usa la última corrección de cada emisión;
    version=original, lo primero que se publicó."""
    if version not in ("final", "original"):
        raise HTTPException(400, "version: final u original")
    if publicacion not in puntajes.PUBLICACIONES:
        raise HTTPException(400, "publicacion: todas, en_el_dia o noche_anterior")
    if etiqueta and etiqueta not in ETIQUETAS:
        raise HTTPException(400, f"etiqueta: una de {', '.join(ETIQUETAS)}")
    hasta = hasta or datetime.now(timezone(timedelta(hours=-3))).date() - timedelta(days=1)
    desde = desde or hasta - timedelta(days=60)
    if desde > hasta or (hasta - desde).days > 3 * 366:
        raise HTTPException(400, "Rango de fechas inválido (máximo 3 años)")
    dias = consultar("""
        SELECT e.ciudad, e.fecha_pronostico, e.capturado, e.publicado_por, e.es_correccion,
               -- Día en que se publicó: el que trae la hoja importada, o el
               -- día (hora argentina) en que el servidor lo vio por primera vez.
               COALESCE((e.contenido -> 'publicado_el' ->> 0)::date,
                        (e.capturado AT TIME ZONE 'America/Argentina/Buenos_Aires')::date) AS publicado_el,
               d.fecha, d.plazo_dias, d.temp_min, d.temp_max, d.temp_min_suburbana,
               d.temp_max_suburbana, d.periodos
        FROM pronostico_dia d JOIN pronostico_emision e ON e.id = d.emision_id
        WHERE e.ciudad = %s AND d.fecha BETWEEN %s AND %s""", (ciudad, desde, hasta))
    observado = {(r["ciudad"], r["fecha"], r["elemento"]): r for r in consultar("""
        SELECT ciudad, fecha, elemento, valor, categoria, estacion_id, detalle FROM verif_observado
        WHERE ciudad = %s AND fecha BETWEEN %s AND %s""", (ciudad, desde - timedelta(days=5), hasta))}
    filas = puntajes.armar_pares(puntajes.elegir_emisiones(dias, version), observado)
    filas = puntajes.filtrar_publicacion(filas, publicacion)
    filas.sort(key=lambda f: (f["fecha"], f["plazo"]))
    # La tabla de situaciones se arma con TODOS los días (si no, no hay "resto"
    # contra qué comparar); el resto de la página, con el filtro de etiqueta.
    situaciones = puntajes.resumen_situaciones(filas)
    filas = puntajes.filtrar_etiqueta(filas, etiqueta)
    if formato == "csv":
        planas = []
        for f in filas:
            p = {k: f[k] for k in ("ciudad", "fecha", "plazo", "fecha_emision", "publicado_el",
                                   "anticipado", "pronosticador")}
            for el in (*puntajes.TEMPERATURAS, "pp", "viento_int"):
                p[f"{el}_pron"], p[f"{el}_obs"] = f[el]["pron"], f[el]["obs"]
            p["viento_dir_pron"] = " ".join(f["viento_dir"]["pron"])
            p["viento_dir_obs"] = f["viento_dir"]["obs"]
            p["viento_kmh_obs"] = f["viento_int"]["obs_kmh"]
            p["etiquetas"] = " ".join(f["etiquetas"] or [])
            planas.append(p)
        return _csv(planas, f"verificacion_{ciudad}_{desde}_{hasta}")
    return {"ciudad": ciudad, "desde": desde, "hasta": hasta, "version": version, "publicacion": publicacion,
            "resumen": puntajes.resumen(filas), "dias": filas,
            "minimas": puntajes.minimas_nocturnas(filas, observado, desde, hasta),
            "etiqueta": etiqueta, "situaciones": situaciones,
            "etiquetas_def": ETIQUETAS, "umbrales": UMBRALES}


# Ubicación de las ciudades que pronostica el grupo (para el mapa del panel).
# El panel muestra las que tienen algún pronóstico guardado: Bolívar, que
# hoy no se pronostica, aparece sola cuando el grupo empiece.
CIUDADES_COORD = {
    "La Plata": (-34.92, -57.95), "Junín": (-34.59, -60.95), "Mar del Plata": (-38.00, -57.56),
    "Bolívar": (-36.23, -61.11), "Tandil": (-37.32, -59.13), "Bahía Blanca": (-38.72, -62.27),
}


# Estación SYNOP (aeropuerto) de cada ciudad, para el link a Ogimet del
# panel: la misma que usa la verificación (config/estaciones.yaml).
CIUDADES_SYNOP = {
    "La Plata": "omm_87593", "Junín": "omm_87548", "Mar del Plata": "omm_87692",
    "Bolívar": "omm_87640", "Tandil": "omm_87645", "Bahía Blanca": "omm_87750",
}


@app.get("/api/cartas/synop")
def synop_del_dia(estacion: str, fecha: date):
    """Lo que midió una estación SINÓPTICA del SMN durante un día argentino
    (00 a 24 hs), para el gráfico del panel del pronóstico en Cartas. Está
    también en la copia pública: por eso solo entrega estaciones sinópticas
    (datos públicos del SMN), nunca las EMAs."""
    if not consultar("SELECT 1 FROM estacion WHERE id = %s AND tipo = 'sinoptica'", (estacion,)):
        raise HTTPException(404, "estación sinóptica desconocida")
    desde = datetime(fecha.year, fecha.month, fecha.day, 3, tzinfo=timezone.utc)   # 00 hs argentina
    # De 00 a 24 hs inclusive (la consulta toma el período "desde exclusive").
    filas = observaciones_sinoptica(estacion, desde=desde - timedelta(seconds=1), hasta=desde + timedelta(days=1))
    for f in filas:
        f.pop("mensaje", None)
    return {"estacion": estacion, "desde": _iso(desde), "hasta": _iso(desde + timedelta(days=1)),
            "filas": list(reversed(filas))}


@app.get("/api/modo")
def modo():
    """"completo" (el visualizador privado) o "cartas" (la copia pública)."""
    return {"modo": WEB_MODO}


@app.get("/api/verificacion/dia")
def verificacion_dia(fecha: date, plazo: int = 0, version: str = "final"):
    """Para analizar un evento: lo que el grupo pronosticó para ESE día en
    todas las ciudades (con plazo 0 = el pronóstico del mismo día, 1 = el
    del día anterior...) y lo que se observó."""
    if version not in ("final", "original"):
        raise HTTPException(400, "version: final u original")
    if not 0 <= plazo <= 6:
        raise HTTPException(400, "plazo: de 0 a 6")
    dias = consultar("""
        SELECT e.ciudad, e.fecha_pronostico, e.capturado, e.publicado_por, e.es_correccion, e.hora_emision,
               COALESCE((e.contenido -> 'publicado_el' ->> 0)::date,
                        (e.capturado AT TIME ZONE 'America/Argentina/Buenos_Aires')::date) AS publicado_el,
               d.fecha, d.plazo_dias, d.temp_min, d.temp_max, d.temp_min_suburbana,
               d.temp_max_suburbana, d.periodos
        FROM pronostico_dia d JOIN pronostico_emision e ON e.id = d.emision_id
        WHERE d.fecha = %s AND d.plazo_dias = %s""", (fecha, plazo))
    observado = {(r["ciudad"], r["fecha"], r["elemento"]): r for r in consultar("""
        SELECT ciudad, fecha, elemento, valor, categoria, estacion_id, detalle FROM verif_observado
        WHERE fecha BETWEEN %s AND %s""", (fecha - timedelta(days=plazo + 1), fecha))}
    elegidos = puntajes.elegir_emisiones(dias, version)
    filas = {f["ciudad"]: f for f in puntajes.armar_pares(elegidos, observado)}
    extra = {d["ciudad"]: d for d in elegidos}
    con_pronosticos = {r["ciudad"] for r in consultar("SELECT DISTINCT ciudad FROM pronostico_emision")}
    ciudades = sorted(con_pronosticos | set(filas), key=lambda c: (c != "La Plata", c))
    salida = []
    for c in ciudades:
        f = filas.get(c)
        if f:
            f = {**f, "periodos": extra[c]["periodos"] or [], "hora_emision": extra[c].get("hora_emision")}
        # Para controlar a mano: la estación del aeropuerto y las que se usaron ese día.
        usadas = [v.get("estacion") for v in (f or {}).values() if isinstance(v, dict) and v.get("estacion")]
        synop = [e for e in dict.fromkeys([CIUDADES_SYNOP.get(c), *usadas]) if e and e.startswith("omm_")]
        salida.append({"ciudad": c, "coord": CIUDADES_COORD.get(c), "synop": synop, "pronostico": f})
    return {"fecha": fecha, "plazo": plazo, "ciudades": salida}


# ---------------------------------------------------------------------------
# Cartas de diagnóstico (imágenes archivadas por el servicio de ingesta)
# ---------------------------------------------------------------------------
CARTAS_CONFIG = os.environ.get("CARTAS_CONFIG", "/config/cartas.yaml")
ARCHIVO_DIR = os.environ.get("ARCHIVO_DIR", "/archivo")


def _cartas_productos():
    import yaml
    try:
        with open(CARTAS_CONFIG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []
    horas = cfg.get("horas_utc", [0, 6, 12, 18])
    return [{**{k: p.get(k) for k in ("id", "nombre", "nivel", "fuente", "zona", "dominio", "solo_con_tormenta")},
             "horas_utc": p.get("horas_utc", horas), "solo_archivo": p.get("descargar", True) is False}
            for p in cfg.get("productos", []) if p.get("activo", True)]


@app.get("/api/cartas/disponibles")
def cartas_disponibles(mes: str | None = None):
    """Horas (UTC) con cartas archivadas: {ts: [productos]}. mes=AAAA-MM para uno solo."""
    from ..cartas import archivo
    if mes and not (len(mes) == 7 and mes[4] == "-" and mes.replace("-", "").isdigit()):
        raise HTTPException(400, "mes: AAAA-MM")
    return archivo.disponibles(ARCHIVO_DIR, [p["id"] for p in _cartas_productos()], mes)


@app.get("/api/cartas")
def cartas(ts: datetime):
    """Las cartas de una hora UTC (ej. ts=2026-10-12T12:00Z), con la dirección
    de cada imagen. Los mapas de Ogimet son transparentes: "fondo" es la
    geografía que va debajo."""
    from ..cartas import archivo
    ts = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    raiz = Path(ARCHIVO_DIR) / "cartas"
    dibujos = _dibujos_de(ts)
    salida = []
    for p in _cartas_productos():
        p["dibujo"] = dibujos.get(p["id"])
        f = archivo.fondo(ARCHIVO_DIR, f"ogimet_{p['zona']}") if p["fuente"] == "ogimet" else None
        p["fondo"] = "/cartas-img/" + f.relative_to(raiz).as_posix() if f else None
        m = archivo.ruta_miniatura(ARCHIVO_DIR, p["id"], ts)
        p["miniatura"] = "/cartas-img/" + m.relative_to(raiz).as_posix() if m.exists() else None
        a = archivo.ruta_areas(ARCHIVO_DIR, p["id"], ts)
        p["areas"] = "/cartas-img/" + a.relative_to(raiz).as_posix() if a.exists() else None
        url = None
        for ext in ("png", "gif", "jpg"):
            f = archivo.ruta(ARCHIVO_DIR, p["id"], ts, ext)
            if f.exists():
                url = "/cartas-img/" + f.relative_to(raiz).as_posix()
                break
        salida.append({**p, "url": url})
    return {"ts": ts, "productos": salida}


@app.get("/api/cartas/pronostico")
def cartas_pronostico():
    """Pronóstico del GFS (cartas de Bentley) de la última corrida guardada:
    por carta, la corrida y la imagen de cada plazo."""
    from ..cartas import pronostico
    salida = []
    for p in _cartas_productos():
        if p["fuente"] != "bentley" or p["solo_archivo"]:
            continue
        d = pronostico.leer(ARCHIVO_DIR, p["id"])
        if not d:
            continue
        base = f"/cartas-img/{pronostico.CARPETA}/{p['id']}/"
        salida.append({"id": p["id"], "nombre": p["nombre"], "nivel": p["nivel"], "inicio": d["inicio"],
                       "cuadros": [{**c, "url": base + c["archivo"]} for c in d["cuadros"]]})
    return {"productos": salida}


@app.get("/api/cartas/estaciones-omm")
def estaciones_omm():
    """Nombre de cada estación sinóptica de Sudamérica, por número OMM (para
    los mapas de Ogimet, que solo dan el número)."""
    from ..cartas import ogimet
    return ogimet.nombres_omm()


@app.get("/api/cartas/observaciones")
def observaciones_de_la_hora(ts: datetime):
    """Lo observado a la hora de la carta: el SYNOP de esa hora de cada
    estación del SMN."""
    ts = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    columnas = ",\n".join(f"max(o.valor) FILTER (WHERE o.variable = '{c}') AS {c}" for c in COLUMNAS_SYNOP)
    smn = consultar(f"""
        SELECT e.id, e.nombre, {columnas}
        FROM estacion e JOIN observacion o ON o.estacion_id = e.id
        WHERE e.tipo = 'sinoptica' AND e.activa AND o.ts = %s AND o.qc <> 4
        GROUP BY e.id, e.nombre ORDER BY e.nombre""", (ts,))
    for f in smn:
        if f["hum"] is None:
            f["hum"] = humedad_relativa(f["temp"], f["td"])
    return {"ts": _iso(ts), "smn": _json(smn)}


# ---------------------------------------------------------------------------
# Dibujos de análisis sobre las cartas
# ---------------------------------------------------------------------------
# Un dibujo es {"ancho", "alto", "elementos": [...]}: coordenadas en un
# lienzo de `ancho` x `alto` (el de la imagen, escalado a 1000 de ancho), así
# se dibuja igual en la placa chica que en el visor grande.
TIPOS_DIBUJO = {"trazo", "frente", "centro", "texto", "flecha"}
MAX_ELEMENTOS = 2000


def _dibujos_de(ts):
    try:
        return {r["producto"]: r["dibujo"] for r in consultar(
            "SELECT producto, dibujo FROM carta_dibujo WHERE ts = %s", (ts,))}
    except Exception:          # la tabla todavía no existe: sin dibujos
        return {}


def _validar_dibujo(d):
    if not isinstance(d, dict) or not isinstance(d.get("elementos"), list):
        raise HTTPException(400, "dibujo: {ancho, alto, elementos: [...]}")
    if len(d["elementos"]) > MAX_ELEMENTOS:
        raise HTTPException(400, f"dibujo: hasta {MAX_ELEMENTOS} elementos")
    for e in d["elementos"]:
        if not isinstance(e, dict) or e.get("tipo") not in TIPOS_DIBUJO:
            raise HTTPException(400, f"dibujo: tipo de elemento desconocido ({str(e)[:40]})")
    for k in ("ancho", "alto"):
        if not isinstance(d.get(k), (int, float)) or not 10 <= d[k] <= 10000:
            raise HTTPException(400, f"dibujo: {k} inválido")
    return {"ancho": d["ancho"], "alto": d["alto"], "elementos": d["elementos"]}


def _producto_valido(producto):
    if producto not in {p["id"] for p in _cartas_productos()}:
        raise HTTPException(404, "carta desconocida")


@app.get("/api/cartas/dibujo")
def leer_dibujo(producto: str, ts: datetime):
    ts = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return {"producto": producto, "ts": _iso(ts), "dibujo": _dibujos_de(ts).get(producto)}


@app.put("/api/cartas/dibujo")
def guardar_dibujo(request: Request, producto: str, ts: datetime, dibujo: dict = Body(...)):
    """Guarda (o, si quedó vacío, borra) el dibujo de una carta. Solo en el
    visualizador completo: en la copia pública no existe la conexión de escritura."""
    if pool_escritura is None:
        raise HTTPException(405, "acá no se puede dibujar")
    _producto_valido(producto)
    ts = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    d = _validar_dibujo(dibujo)
    autor = _usuario(request.headers.get("authorization", ""))
    with pool_escritura.connection() as conn:
        if not d["elementos"]:
            conn.execute("DELETE FROM carta_dibujo WHERE producto = %s AND ts = %s", (producto, ts))
        else:
            conn.execute("""
                INSERT INTO carta_dibujo (producto, ts, dibujo, autor) VALUES (%s, %s, %s, %s)
                ON CONFLICT (producto, ts) DO UPDATE
                SET dibujo = EXCLUDED.dibujo, autor = EXCLUDED.autor, actualizado = now()""",
                         (producto, ts, Jsonb(d), autor))
    return {"ok": True, "elementos": len(d["elementos"])}


# ---------------------------------------------------------------------------
# Bitácora de las estaciones: cortes, fallas y mantenimiento
# ---------------------------------------------------------------------------
# Los CORTES (huecos en los datos) se calculan solos a partir de las
# observaciones. La CAUSA no se puede adivinar: se anota a mano en la tabla
# estacion_incidente, y después se cruza con los cortes para saber cuántas
# horas se perdieron por cada motivo.
CAUSAS = {
    # Fallas
    "internet": "Corte de internet / WiFi",
    "energia": "Corte de luz / batería",
    "equipo": "Falla del equipo (placa, consola, datalogger)",
    "sensor": "Falla de un sensor",
    "plataforma": "Falla de la plataforma (ThingSpeak, Wunderground, Facultad)",
    "servidor": "Falla de nuestro servidor",
    "clima": "Daño por tormenta, viento, rayo",
    "vandalismo": "Robo o vandalismo",
    "desconocida": "No se sabe",
    # Mantenimiento (planificado)
    "mantenimiento": "Mantenimiento / limpieza",
    "calibracion": "Calibración o cambio de sensor",
    "traslado": "Traslado o reinstalación",
    "otra": "Otra",
}
TOLERANCIA = timedelta(minutes=30)   # restos menores de un corte con causa: se le suman
MANTENIMIENTO = {"mantenimiento", "calibracion", "traslado"}
DATOS_DURANTE = {"sin_datos": "Sin datos", "dudosos": "Datos dudosos", "normales": "Datos normales"}


def _utc(t):
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _escritura():
    if pool_escritura is None:
        raise HTTPException(405, "acá no se puede modificar nada")
    return pool_escritura


def _validar_incidente(d):
    if not isinstance(d, dict):
        raise HTTPException(400, "se esperaba un objeto")
    if d.get("causa") not in CAUSAS:
        raise HTTPException(400, f"causa desconocida: {d.get('causa')}")
    datos = d.get("datos") or "sin_datos"
    if datos not in DATOS_DURANTE:
        raise HTTPException(400, f"datos: {', '.join(DATOS_DURANTE)}")
    try:
        desde = _utc(datetime.fromisoformat(str(d["desde"]).replace("Z", "+00:00")))
        hasta = _utc(datetime.fromisoformat(str(d["hasta"]).replace("Z", "+00:00"))) if d.get("hasta") else None
    except (KeyError, ValueError):
        raise HTTPException(400, "desde/hasta: fecha y hora inválidas")
    if hasta and hasta <= desde:
        raise HTTPException(400, "«hasta» tiene que ser posterior a «desde»")
    estacion = d.get("estacion_id") or None
    if estacion and not consultar("SELECT 1 FROM estacion WHERE id = %s", (estacion,)):
        raise HTTPException(400, f"estación desconocida: {estacion}")
    texto = (d.get("descripcion") or "").strip()[:2000] or None
    return {"estacion_id": estacion, "causa": d["causa"], "desde": desde, "hasta": hasta,
            "datos": datos, "descripcion": texto}


@app.get("/api/incidentes/causas")
def causas_de_incidentes():
    return {"causas": CAUSAS, "mantenimiento": sorted(MANTENIMIENTO), "datos": DATOS_DURANTE}


def _incidentes(desde, hasta, estacion=None):
    return consultar("""
        SELECT i.id, i.estacion_id, coalesce(e.nombre, 'Toda la red') AS estacion, i.causa, i.desde, i.hasta,
               i.datos, i.descripcion, i.autor, i.actualizado
        FROM estacion_incidente i LEFT JOIN estacion e ON e.id = i.estacion_id
        WHERE i.desde < %s AND coalesce(i.hasta, 'infinity') > %s
          AND (%s::text IS NULL OR i.estacion_id = %s OR i.estacion_id IS NULL)
        ORDER BY i.desde DESC""", (hasta, desde, estacion, estacion))


@app.get("/api/incidentes")
def listar_incidentes(dias: float = 30, desde: datetime | None = None, hasta: datetime | None = None,
                      estacion: str | None = None, formato: str = "json"):
    """Lo anotado en la bitácora que cae (aunque sea en parte) en el período."""
    desde, hasta = _rango(dias, desde, hasta, 3660)
    filas = _incidentes(desde, hasta, estacion)
    if formato == "csv":
        return _csv(filas, "bitacora_estaciones")
    return _json(filas)


@app.post("/api/incidentes")
def crear_incidente(request: Request, incidente: dict = Body(...)):
    d = _validar_incidente(incidente)
    autor = _usuario(request.headers.get("authorization", ""))
    with _escritura().connection() as conn:
        fila = conn.execute("""
            INSERT INTO estacion_incidente (estacion_id, causa, desde, hasta, datos, descripcion, autor)
            VALUES (%(estacion_id)s, %(causa)s, %(desde)s, %(hasta)s, %(datos)s, %(descripcion)s, %(autor)s)
            RETURNING id""", {**d, "autor": autor}).fetchone()
    return {"ok": True, "id": fila["id"]}


@app.put("/api/incidentes/{incidente_id}")
def editar_incidente(request: Request, incidente_id: int, incidente: dict = Body(...)):
    d = _validar_incidente(incidente)
    autor = _usuario(request.headers.get("authorization", ""))
    with _escritura().connection() as conn:
        n = conn.execute("""
            UPDATE estacion_incidente SET estacion_id = %(estacion_id)s, causa = %(causa)s, desde = %(desde)s,
                   hasta = %(hasta)s, datos = %(datos)s, descripcion = %(descripcion)s,
                   autor = coalesce(%(autor)s, autor), actualizado = now()
            WHERE id = %(id)s""", {**d, "autor": autor, "id": incidente_id}).rowcount
    if not n:
        raise HTTPException(404, "no existe")
    return {"ok": True, "id": incidente_id}


@app.delete("/api/incidentes/{incidente_id}")
def borrar_incidente(incidente_id: int):
    with _escritura().connection() as conn:
        n = conn.execute("DELETE FROM estacion_incidente WHERE id = %s", (incidente_id,)).rowcount
    if not n:
        raise HTTPException(404, "no existe")
    return {"ok": True}


def _restar(tramos, a, b):
    """Saca el intervalo [a, b) de una lista de tramos [(inicio, fin)]: devuelve
    (lo que queda, cuánto se sacó)."""
    quedan, sacado = [], timedelta(0)
    for x, y in tramos:
        if b <= x or a >= y:
            quedan.append((x, y))
            continue
        sacado += min(y, b) - max(x, a)
        if x < a:
            quedan.append((x, a))
        if b < y:
            quedan.append((b, y))
    return quedan, sacado


def cortes_de_estacion(huecos, primero, ultimo, hay_previos, desde, hasta, umbral):
    """Arma los cortes de UNA estación en [desde, hasta).

    huecos: [(último dato antes del hueco, primer dato después)] más largos que
    el umbral. primero/ultimo: primer y último dato de la ventana consultada.
    hay_previos: si la estación tenía datos antes de la ventana (si no, el
    período "empieza" cuando empezó la estación: antes no estaba caída, no existía).
    Devuelve (inicio efectivo del período, [(inicio, fin, sigue)])."""
    if primero is None:
        return (desde, [(desde, hasta, True)]) if hay_previos else (None, [])
    inicio = desde
    cortes = []
    if primero > desde:
        if hay_previos:
            if primero - desde > umbral:
                cortes.append((desde, primero, False))
        else:
            inicio = primero
    for a, b in huecos:
        a, b = max(a, inicio), min(b, hasta)
        if b > a:
            cortes.append((a, b, False))
    if hasta - ultimo > umbral:
        cortes.append((max(ultimo, inicio), hasta, True))
    return inicio, cortes


@app.get("/api/estaciones/disponibilidad")
def disponibilidad(dias: float = 30, desde: datetime | None = None, hasta: datetime | None = None,
                   umbral_min: int = 60, tipo: str = "ema"):
    """Cuánto tiempo estuvo cada estación sin mandar datos, cada corte, y
    cuántas horas se explican por cada causa anotada en la bitácora.

    Un "corte" es un hueco de más de `umbral_min` minutos entre dos datos
    seguidos (de cualquier variable). Lo que no tiene una causa anotada que lo
    cubra queda como "sin_explicar"."""
    desde, hasta = _rango(dias, desde, hasta, 400)
    hasta = min(hasta, datetime.now(timezone.utc))
    umbral = timedelta(minutes=max(15, min(umbral_min, 24 * 60)))
    mirar_desde = desde - timedelta(days=30)   # para saber si ya venía caída
    est = consultar("SELECT id, nombre FROM estacion WHERE tipo = %s AND activa ORDER BY nombre", (tipo,))
    ids = [e["id"] for e in est]
    huecos = consultar("""
        WITH t AS (
            SELECT DISTINCT estacion_id, ts FROM observacion
            WHERE estacion_id = ANY(%s) AND ts >= %s AND ts < %s AND qc <> 4
        )
        SELECT estacion_id, antes, despues FROM (
            SELECT estacion_id, lag(ts) OVER (PARTITION BY estacion_id ORDER BY ts) AS antes, ts AS despues FROM t
        ) l WHERE despues - antes > %s AND despues > %s
        ORDER BY 1, 2""", (ids, mirar_desde, hasta, umbral, desde))
    bordes = {f["estacion_id"]: f for f in consultar("""
        SELECT estacion_id, min(ts) AS primero, max(ts) AS ultimo FROM observacion
        WHERE estacion_id = ANY(%s) AND ts >= %s AND ts < %s AND qc <> 4 GROUP BY 1""", (ids, mirar_desde, hasta))}
    incidentes = _incidentes(desde, hasta)
    ahora = datetime.now(timezone.utc)

    salida, totales = [], {}
    for e in est:
        b = bordes.get(e["id"], {})
        primero = b.get("primero")
        previos = True
        if primero is None or primero > desde:     # ¿estaba caída desde antes, o recién empezó?
            previos = bool(consultar("SELECT 1 FROM observacion WHERE estacion_id = %s AND ts < %s LIMIT 1",
                                     (e["id"], mirar_desde)))
        inicio, cortes = cortes_de_estacion(
            [(h["antes"], h["despues"]) for h in huecos if h["estacion_id"] == e["id"]],
            primero, b.get("ultimo"), previos, desde, hasta, umbral)
        if inicio is None:
            continue    # todavía no mandó nunca nada
        propios = sorted((i for i in incidentes if i["estacion_id"] in (e["id"], None)), key=lambda i: i["desde"])
        por_causa, sin_explicar, lista = {}, timedelta(0), []
        for a, z, sigue in cortes:
            tramos = [(a, z)]
            causas = []
            for i in propios:
                tramos, h = _restar(tramos, i["desde"], i["hasta"] or ahora)
                if h:
                    por_causa[i["causa"]] = por_causa.get(i["causa"], timedelta(0)) + h
                    causas.append({"id": i["id"], "causa": i["causa"]})
            resto = sum((y - x for x, y in tramos), timedelta(0))
            if causas and resto and all(y - x < TOLERANCIA for x, y in tramos):
                # La causa cubre el corte salvo unos minutos en los bordes (se
                # anotó "a las 8" y el último dato fue 7:50): es de esa causa.
                principal = causas[0]["causa"]
                por_causa[principal] += resto
                resto = timedelta(0)
            sin_explicar += resto
            lista.append({"desde": _iso(a), "hasta": _iso(z), "sigue": sigue,
                          "horas": round((z - a) / timedelta(hours=1), 1), "causas": causas,
                          "horas_sin_explicar": round(resto / timedelta(hours=1), 1)})
        perdido = sum((z - a for a, z, _ in cortes), timedelta(0))
        periodo = hasta - inicio
        h = lambda t: round(t / timedelta(hours=1), 1)
        for c, t in por_causa.items():
            totales[c] = totales.get(c, timedelta(0)) + t
        if sin_explicar:
            totales["sin_explicar"] = totales.get("sin_explicar", timedelta(0)) + sin_explicar
        salida.append({
            "id": e["id"], "nombre": e["nombre"], "desde": _iso(inicio),
            "horas_periodo": h(periodo), "horas_sin_datos": h(perdido),
            "disponibilidad": round(100 * (1 - perdido / periodo), 1) if periodo else None,
            "cortes": len(cortes), "por_causa": {c: h(t) for c, t in por_causa.items()},
            "horas_sin_explicar": h(sin_explicar),
            "mantenimientos": sum(1 for i in propios if i["causa"] in MANTENIMIENTO and i["estacion_id"] == e["id"]),
            "lista_cortes": lista[::-1][:200],
        })
    return {"desde": _iso(desde), "hasta": _iso(hasta), "umbral_min": int(umbral / timedelta(minutes=1)),
            "estaciones": salida,
            "por_causa": {c: round(t / timedelta(hours=1), 1) for c, t in sorted(totales.items(), key=lambda x: -x[1])}}


@app.exception_handler(Exception)
def _error(_request, exc):
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# La página y sus archivos. Va al final para que /api/... tenga prioridad.
app.mount("/cartas-img", StaticFiles(directory=Path(ARCHIVO_DIR) / "cartas", check_dir=False), name="cartas")
app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="web")
