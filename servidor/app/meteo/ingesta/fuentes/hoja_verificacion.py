"""
Historial de pronósticos desde la hoja Verificacion_Pronostico del equipo.

El servidor guarda cada emisión desde que se puso en marcha; lo anterior
está en esa hoja (exportada como CSV). Una fila por (ciudad, fecha
pronosticada, plazo) con lo YA RESUMIDO por el Apps Script (precip SI/NO,
direcciones "W, SW", intensidades "LEVES, MODERADOS"...), no los períodos
originales. Para no tener dos caminos de cálculo, cada fila se convierte en
un período "día" sintético que pronostico.derivar() entiende igual.

Ojo con las fechas: `fecha_emision` es el día en que se PUBLICÓ y `plazo`
es el bloque del pronóstico (Día 2 = plazo 1). Si alguien publica la noche
anterior, fecha_pronostico - fecha_emision = plazo + 1. Se usa el plazo
del bloque (fecha nominal = fecha_pronostico - plazo), que es como cuenta
el servidor las emisiones que captura; la fecha real de publicación queda
guardada en el contenido ("publicado_el").

La hoja solo guarda la versión vigente (las correcciones pisan la fila) y
no tiene plazo 0.
"""
import csv
import io
from collections import Counter, defaultdict
from datetime import date, timedelta

NOMBRES_DIR = {"N": "norte", "NE": "noreste", "E": "este", "SE": "sudeste",
               "S": "sur", "SW": "sudoeste", "W": "oeste", "NW": "noroeste"}
ORIGEN = "hoja Verificacion_Pronostico"


def _lista(texto):
    return [x.strip() for x in (texto or "").split(",") if x.strip()]


def periodo_sintetico(f):
    precip = None
    if f.get("precip", "").upper() == "SI":
        precip = f.get("precip_tipo") or "precipitación"
        if f.get("tormenta", "").upper() == "SI" and "tormenta" not in precip.lower():
            precip += ", tormentas"
    return {
        "period": "día",
        "precipitation_description": precip,
        "probability_of_precipitation": None,
        "sky_condition": "niebla" if f.get("niebla", "").upper() == "SI" else None,
        "temperature": None,
        "wind_direction": " ".join(NOMBRES_DIR.get(d.upper(), d.lower()) for d in _lista(f.get("v_dir"))) or None,
        "wind_intensity": " ".join(i.lower() for i in _lista(f.get("v_int"))) or None,
    }


def leer_csv(texto):
    """CSV de la hoja -> emisiones con el mismo formato JSON que el Apps
    Script (las que guarda pronosticos.guardar)."""
    grupos = defaultdict(list)
    for f in csv.DictReader(io.StringIO(texto.lstrip("﻿"))):
        try:
            dia = date.fromisoformat(f["fecha_pronostico"].strip())
            plazo = int(f["plazo"])
        except (KeyError, ValueError):
            continue
        grupos[(f["ciudad"].strip(), dia - timedelta(days=plazo))].append(f)

    emisiones = []
    for (ciudad, nominal), filas in sorted(grupos.items()):
        filas.sort(key=lambda f: f["fecha_pronostico"])
        emisiones.append({
            "city": ciudad,
            "forecast_date": nominal.isoformat(),
            "emission_time": None,
            "publicado_por": Counter(f["pronosticador"] for f in filas).most_common(1)[0][0] or None,
            "es_correccion": any(f.get("es_correccion", "").upper() == "SI" for f in filas),
            "origen": ORIGEN,
            "publicado_el": sorted({f["fecha_emision"] for f in filas}),
            "daily_forecasts": [{
                "date": f["fecha_pronostico"].strip(),
                "temp_min": f.get("tmin_urb") or None, "temp_max": f.get("tmax_urb") or None,
                "temp_min_suburban": f.get("tmin_sub") or None, "temp_max_suburban": f.get("tmax_sub") or None,
                "custom_description": f.get("comentarios") or None,
                "min_noc": f.get("min_noc") or None,
                "period_forecasts": [periodo_sintetico(f)],
            } for f in filas],
        })
    return emisiones


def importar(conn, texto):
    """Guarda las emisiones de la hoja. Las que el servidor ya capturó en
    vivo se saltean (las capturadas tienen los períodos completos).
    Devuelve (nuevas, salteadas)."""
    from . import pronosticos

    emisiones = leer_csv(texto)
    with conn.cursor() as cur:
        # Las completas (capturadas en vivo o de la hoja "Pronosticos") ganan.
        cur.execute("SELECT ciudad, fecha_pronostico FROM pronostico_emision WHERE contenido ->> 'origen' IS DISTINCT FROM %s",
                    (ORIGEN,))
        en_vivo = {(c, f.isoformat()) for c, f in cur.fetchall()}
    nuevas = [e for e in emisiones if (e["city"], e["forecast_date"]) not in en_vivo]
    return pronosticos.guardar(conn, nuevas), len(emisiones) - len(nuevas)
