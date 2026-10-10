"""
Davis Vantage (Observatorio FCAG): archivo de texto que exporta WeatherLink
("downld08.txt"), con una fila cada 5 minutos y columnas de ancho variable:

  Date    Time   Temp Out  Hi Temp  Low Temp  Out Hum  Dew Pt.  Wind Speed  Wind Dir ...
 3/10/26   0:05    14.6      14.6      14.4      70      9.1      0.0       ---

- Fecha día/mes/año (2 dígitos) y hora LOCAL (Argentina); la hora marca el
  FINAL del intervalo de 5 minutos.
- "Rain" es lo que llovió en ese intervalo (mm).
- "---" = sin dato.
"""
from datetime import datetime

import urllib3

from ..base import HORA_ARG, KMH_A_MS, Obs, num

# Orden de las columnas del archivo (28 columnas).
COLUMNAS = [
    "fecha", "hora", "temp", "temp_max_int", "temp_min_int", "hum", "td", "viento_vel",
    "viento_dir_txt", "recorrido", "racha", "racha_dir", "sens_viento", "indice_calor",
    "thw", "presion", "precip", "intens_precip", "gd_calor", "gd_frio", "temp_int",
    "hum_int", "td_int", "calor_int", "muestras_viento", "tx_viento", "recepcion", "intervalo",
]

# Rumbos de 16 direcciones -> grados.
RUMBOS = {r: i * 22.5 for i, r in enumerate(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}


def parsear(texto, estacion_id, unidad_viento="km/h", presion_es="pnm"):
    factor_viento = KMH_A_MS if unidad_viento == "km/h" else 1.0
    obs = []
    for linea in texto.splitlines():
        partes = linea.split()
        if len(partes) != len(COLUMNAS) or "/" not in partes[0]:
            continue  # encabezados, separadores, líneas cortadas
        fila = dict(zip(COLUMNAS, partes))
        try:
            ts = datetime.strptime(f"{fila['fecha']} {fila['hora']}", "%d/%m/%y %H:%M").replace(tzinfo=HORA_ARG)
        except ValueError:
            continue

        def agregar(variable, valor, factor=1.0):
            v = num(valor)
            if v is not None:
                obs.append(Obs(ts, estacion_id, variable, round(v * factor, 3)))

        agregar("temp", fila["temp"])
        agregar("hum", fila["hum"])
        agregar("td", fila["td"])
        agregar("viento_vel", fila["viento_vel"], factor_viento)
        agregar("racha", fila["racha"], factor_viento)
        if fila["viento_dir_txt"] in RUMBOS:
            obs.append(Obs(ts, estacion_id, "viento_dir", RUMBOS[fila["viento_dir_txt"]]))
        agregar(presion_es, fila["presion"])
        agregar("precip", fila["precip"])
        agregar("intens_precip", fila["intens_precip"])
    return obs


def bajar(cfg, sesion):
    """Intenta la URL de la Facultad y, si no responde, la copia de respaldo
    (la que el GitHub Action del repo actualiza cada 15 min)."""
    errores = []
    verificar = cfg.get("verificar_ssl", True)
    if not verificar:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    for url, verif in ((cfg["url"], verificar), (cfg.get("url_respaldo"), True)):
        if not url:
            continue
        try:
            r = sesion.get(url, timeout=(10, 60), verify=verif)
            r.raise_for_status()
            return r.text
        except Exception as e:
            errores.append(f"{url}: {type(e).__name__}")
    raise ConnectionError("Ninguna URL de la Davis respondió: " + " | ".join(errores))


def traer(estacion, desde, hasta, sesion):
    """El archivo trae los últimos días completos; se filtra por rango."""
    cfg = estacion["config"]
    obs = parsear(bajar(cfg, sesion), estacion["id"],
                  cfg.get("unidad_viento", "km/h"), cfg.get("presion_es", "pnm"))
    return [o for o in obs if desde <= o.ts <= hasta]
