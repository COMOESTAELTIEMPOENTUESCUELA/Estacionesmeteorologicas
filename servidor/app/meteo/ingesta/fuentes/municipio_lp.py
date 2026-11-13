"""
Red de EMAs de la Municipalidad de La Plata: https://clima.laplata.gob.ar/pub

API pública (sin usuario): https://clima.laplata.gob.ar/api/v1/public/stations
devuelve TODAS las estaciones con su ÚLTIMO valor de cada variable (no hay
historial): el servidor la consulta cada 1 h (frecuencias_min en
estaciones.yaml) y va armando la serie, con un dato por hora. Un solo pedido trae las 17 estaciones: se guarda
la respuesta unos segundos para no pedirla una vez por estación.

Variables (ejemplo del 12/11/2026):
    temperature °C · relative_humidity % · pressure hPa · wind_speed km/h ·
    wind_direction ° · rain_rate mm/h · rain_accumulated mm (un contador)
- La presión se toma como presión a nivel de ESTACIÓN: entre estaciones de la
  misma ciudad difiere 2-3 hPa, como esperable por la altura de cada una (si
  fuera reducida al nivel del mar, darían casi igual). VERIFICAR con la
  Municipalidad.
- wind_speed viene en km/h (valores de 2 a 50 en un día ventoso; en m/s serían
  ráfagas de huracán): se pasa a m/s, como el resto del servidor.
- rain_accumulated es un contador (no se sabe todavía cuándo vuelve a cero):
  se guarda tal cual (precip_acum) y la lluvia de cada intervalo (precip) sale
  de la diferencia con el valor anterior. Si baja, se reinició: lo que marca
  es lo llovido desde el reinicio. Después de un corte de más de 3 horas, ese
  intervalo no se cuenta (no se sabe cuándo llovió). Ver postproceso().
- Valores con "suspect": true se guardan marcados como sospechosos (qc = 3);
  los que vienen con "missing" no se guardan.
"""
import time
from datetime import datetime, timedelta

from ..base import Obs, num

URL = "https://clima.laplata.gob.ar/api/v1/public/stations"
KMH_A_MS = 1 / 3.6
# variable de la API -> (variable del servidor, factor)
MAPEO = {
    "temperature": ("temp", 1.0),
    "relative_humidity": ("hum", 1.0),
    "pressure": ("pres_est", 1.0),
    "wind_speed": ("viento_vel", KMH_A_MS),
    "wind_direction": ("viento_dir", 1.0),
    "rain_rate": ("intens_precip", 1.0),
    "rain_accumulated": ("precip_acum", 1.0),
}
CACHE_SEG = 60
# Si entre dos lecturas del contador pasó más que esto (servidor apagado, API
# caída), la diferencia NO se convierte en lluvia: no se sabe cuándo cayó, y
# ponerla toda en un solo intervalo inventaría un chaparrón. Con la consulta
# cada 1 h, entre lecturas normales hay ~1 h: el margen deja pasar alguna
# demora de la ingesta.
HUECO_MAXIMO = timedelta(hours=3)
_cache = {"t": 0.0, "datos": None}


def pedir(sesion):
    """La respuesta de la API, reusada durante CACHE_SEG (una vuelta de la ingesta)."""
    if _cache["datos"] is None or time.time() - _cache["t"] > CACHE_SEG:
        r = sesion.get(URL, timeout=60)
        r.raise_for_status()
        _cache.update(t=time.time(), datos=r.json())
    return _cache["datos"]


def parsear(datos, estacion_id, codigo):
    """Las observaciones de UNA estación (por su código, ej. "EMA-165")."""
    obs = []
    for est in (datos or {}).get("stations") or []:
        if est.get("code") != codigo:
            continue
        for v in est.get("values") or []:
            destino = MAPEO.get(v.get("variable"))
            valor = num(v.get("value"))
            if not destino or valor is None or v.get("missing") or not v.get("at"):
                continue
            variable, factor = destino
            ts = datetime.fromisoformat(v["at"].replace("Z", "+00:00"))
            obs.append(Obs(ts, estacion_id, variable, round(valor * factor, 4), 3 if v.get("suspect") else 0))
    return obs


def traer(estacion, desde, hasta, sesion):
    obs = parsear(pedir(sesion), estacion["id"], estacion["config"]["codigo"])
    return [o for o in obs if desde <= o.ts <= hasta]


def lluvia_de_contador(anterior, lecturas):
    """lecturas: [(ts, contador)] en orden. anterior: (ts, contador) del último
    ya guardado, o None. -> [(ts, mm caídos desde la lectura anterior)].
    Si el contador baja, se reinició: lo llovido es lo que marca ahora. Si la
    lectura anterior es de hace más de HUECO_MAXIMO, ese intervalo se saltea."""
    salida = []
    for ts, acum in lecturas:
        if anterior is not None and ts - anterior[0] <= HUECO_MAXIMO:
            previo = anterior[1]
            salida.append((ts, round(acum - previo if acum >= previo else acum, 2)))
        anterior = (ts, acum)
    return salida


def postproceso(conn, estacion, obs):
    """Agrega `precip` (mm de cada intervalo) a partir del contador
    `precip_acum`, comparando con el último contador guardado en la base."""
    lecturas = sorted((o.ts, o.valor) for o in obs if o.variable == "precip_acum")
    if not lecturas:
        return obs
    with conn.cursor() as cur:
        cur.execute("""SELECT ts, valor FROM observacion WHERE estacion_id = %s AND variable = 'precip_acum'
                       AND ts < %s ORDER BY ts DESC LIMIT 1""", (estacion["id"], lecturas[0][0]))
        fila = cur.fetchone()
    anterior = None if not fila else (tuple(fila) if isinstance(fila, tuple) else (fila["ts"], fila["valor"]))
    return obs + [Obs(ts, estacion["id"], "precip", mm) for ts, mm in lluvia_de_contador(anterior, lecturas)]
