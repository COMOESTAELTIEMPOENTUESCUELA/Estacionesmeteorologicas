"""
Reportes de la comunidad (formulario de ciencia-ciudadana.html).

Se lee la pestaña "Reportes" de la planilla (la que ya es pública y lee el
sitio) en formato CSV, mediante la API de visualización de Google:

    https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&gid=0

Las columnas se buscan por NOMBRE de encabezado (no por posición), así que
agregar columnas nuevas a la planilla no rompe nada. Las columnas
"Instrumento" y "Periodo" son las nuevas que propone el formulario para
saber con qué se midió la lluvia; si todavía no existen, quedan "sin_dato".
"""
import csv
import hashlib
import io
import re
from datetime import datetime

from ..base import HORA_ARG, num

URL = "https://docs.google.com/spreadsheets/d/{sheet}/gviz/tq"

# Texto que puede aparecer en la planilla -> valor normalizado de la base.
INSTRUMENTOS = {
    "pluviometro_convencional": "pluviometro_convencional",
    "pluviómetro convencional": "pluviometro_convencional",
    "pluviometro convencional": "pluviometro_convencional",
    "pluviometro_casero": "pluviometro_casero",
    "pluviómetro casero": "pluviometro_casero",
    "pluviometro casero": "pluviometro_casero",
    "estacion_automatica": "estacion_automatica",
    "estación automática": "estacion_automatica",
    "estacion automatica": "estacion_automatica",
    "estimacion": "estimacion",
    "estimación": "estimacion",
}
PERIODOS = {
    "24h_9hs": "24h_9hs", "24 h hasta las 9": "24h_9hs",
    "desde_ultima_lectura": "desde_ultima_lectura", "desde la última lectura": "desde_ultima_lectura",
    "evento": "evento", "solo esta lluvia": "evento",
}


def parsear_fecha(texto):
    """Las fechas de la planilla pueden venir como '10/10/2026 14:23:11',
    '10/10/2026', '2026-10-10 14:23:11'... Se interpretan en hora argentina."""
    t = (texto or "").strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=HORA_ARG)
        except ValueError:
            pass
    m = re.match(r"Date\((\d+),(\d+),(\d+)(?:,(\d+),(\d+),(\d+))?\)", t)
    if m:  # formato interno de gviz, mes 0-indexado
        a, mes, d, hh, mm, ss = (int(x) if x else 0 for x in m.groups())
        return datetime(a, mes + 1, d, hh, mm, ss, tzinfo=HORA_ARG)
    return None


def _normalizar(texto, tabla):
    return tabla.get((texto or "").strip().lower(), "sin_dato")


def parsear_csv(texto, escuela_a_estacion=None):
    escuela_a_estacion = escuela_a_estacion or {}
    lector = csv.DictReader(io.StringIO(texto))
    reportes = []
    for fila in lector:
        f = {k.strip().lower(): (v or "").strip() for k, v in fila.items() if k}
        ts = parsear_fecha(f.get("fecha"))
        if ts is None or not (f.get("resumen") or f.get("escuela")):
            continue
        escuela = f.get("escuela") or None
        rep = {
            "ts": ts,
            "tipo": (f.get("tipo") or "otro").lower(),
            "escuela": escuela,
            "reportero": f.get("reportero") or None,
            "lluvia_mm": num(f.get("lluvia_mm")),
            "instrumento": _normalizar(f.get("instrumento"), INSTRUMENTOS),
            "periodo": _normalizar(f.get("periodo"), PERIODOS),
            "estacion_id": escuela_a_estacion.get(escuela),
            "estado_camino": f.get("estado_camino") or None,
            "resumen": f.get("resumen") or None,
            "imagen_url": f.get("imagen_url") or None,
            "lat": num(f.get("lat")),
            "lon": num(f.get("lon")),
        }
        clave = "|".join(str(rep[k]) for k in ("ts", "escuela", "reportero", "tipo", "resumen"))
        rep["huella"] = hashlib.sha256(clave.encode()).hexdigest()[:24]
        reportes.append(rep)
    return reportes


def traer(cfg, sesion):
    r = sesion.get(URL.format(sheet=cfg["sheet_id"]),
                   params={"tqx": "out:csv", "gid": cfg.get("gid", "0")}, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return parsear_csv(r.text, cfg.get("escuela_a_estacion"))


SQL = """
INSERT INTO reporte_comunidad (ts, tipo, escuela, reportero, lluvia_mm, instrumento, periodo,
    estacion_id, estado_camino, resumen, imagen_url, lat, lon, huella)
VALUES (%(ts)s, %(tipo)s, %(escuela)s, %(reportero)s, %(lluvia_mm)s, %(instrumento)s, %(periodo)s,
    %(estacion_id)s, %(estado_camino)s, %(resumen)s, %(imagen_url)s, %(lat)s, %(lon)s, %(huella)s)
ON CONFLICT (huella) DO UPDATE SET
    lluvia_mm = EXCLUDED.lluvia_mm, instrumento = EXCLUDED.instrumento,
    periodo = EXCLUDED.periodo, estacion_id = EXCLUDED.estacion_id
"""


def guardar(conn, reportes):
    with conn.cursor() as cur:
        cur.executemany(SQL, reportes)
        n = cur.rowcount
    conn.commit()
    return max(n, 0)
