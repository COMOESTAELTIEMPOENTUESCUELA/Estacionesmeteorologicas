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

# La ÚNICA escritura de la web: los dibujos de análisis sobre las cartas.
# Va por una conexión aparte, para que todo lo demás siga en solo lectura.
# En la copia pública (modo cartas) no existe: ahí no se escribe nada.
pool_dibujos = None if os.environ.get("WEB_MODO", "completo") == "cartas" else ConnectionPool(
    DSN, min_size=0, max_size=2, open=False,
    kwargs={"options": "-c statement_timeout=10000", "row_factory": dict_row},
)
SQL_DIBUJOS = (Path(__file__).parent / "dibujos.sql")


@asynccontextmanager
async def ciclo_de_vida(_app):
    pool.open()   # al arrancar
    if pool_dibujos:
        pool_dibujos.open()
        try:
            with pool_dibujos.connection() as conn:
                conn.execute(SQL_DIBUJOS.read_text(encoding="utf-8"))
        except Exception as ex:      # sin la tabla, la web anda igual (sin dibujos)
            logging.getLogger(__name__).warning("tabla de dibujos: %s", ex)
    yield
    pool.close()  # al apagar
    if pool_dibujos:
        pool_dibujos.close()


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
            if WEB_MODO == "cartas":
                f.pop("pronosticador", None)       # en la copia pública no van nombres de personas
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
    return [{**{k: p.get(k) for k in ("id", "nombre", "nivel", "fuente", "zona", "dominio")}, "horas_utc": p.get("horas_utc", horas)}
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
        url = None
        for ext in ("png", "gif", "jpg"):
            f = archivo.ruta(ARCHIVO_DIR, p["id"], ts, ext)
            if f.exists():
                url = "/cartas-img/" + f.relative_to(raiz).as_posix()
                break
        salida.append({**p, "url": url})
    return {"ts": ts, "productos": salida}


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
    if pool_dibujos is None:
        raise HTTPException(405, "acá no se puede dibujar")
    _producto_valido(producto)
    ts = (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    d = _validar_dibujo(dibujo)
    autor = _usuario(request.headers.get("authorization", ""))
    with pool_dibujos.connection() as conn:
        if not d["elementos"]:
            conn.execute("DELETE FROM carta_dibujo WHERE producto = %s AND ts = %s", (producto, ts))
        else:
            conn.execute("""
                INSERT INTO carta_dibujo (producto, ts, dibujo, autor) VALUES (%s, %s, %s, %s)
                ON CONFLICT (producto, ts) DO UPDATE
                SET dibujo = EXCLUDED.dibujo, autor = EXCLUDED.autor, actualizado = now()""",
                         (producto, ts, Jsonb(d), autor))
    return {"ok": True, "elementos": len(d["elementos"])}


@app.exception_handler(Exception)
def _error(_request, exc):
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# La página y sus archivos. Va al final para que /api/... tenga prioridad.
app.mount("/cartas-img", StaticFiles(directory=Path(ARCHIVO_DIR) / "cartas", check_dir=False), name="cartas")
app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="web")
