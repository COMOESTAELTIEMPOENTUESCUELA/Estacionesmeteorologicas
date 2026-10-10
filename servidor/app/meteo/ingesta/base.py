"""
Piezas comunes a todos los recolectores.

Cada recolector (ThingSpeak, Wunderground, ...) hace solo una cosa: traer
datos de su fuente y devolverlos como una lista de `Obs`. No sabe nada de la
base de datos. Eso permite probarlo con datos de ejemplo (tests) y cambiar
la base sin tocar los recolectores.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

# Argentina usa UTC-3 todo el año (sin horario de verano desde 2009).
HORA_ARG = timezone(timedelta(hours=-3))

# Factores de conversión a unidades del sistema internacional.
KMH_A_MS = 1 / 3.6
NUDOS_A_MS = 0.514444


@dataclass(frozen=True)
class Obs:
    ts: datetime  # con zona horaria (aware), siempre
    estacion_id: str
    variable: str
    valor: float
    qc: int = 0  # 0 sin controlar, 1 bueno, 3 sospechoso, 4 malo


def num(x):
    """Convierte a float tolerando vacíos, '---', None, comas decimales."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", ".")
    if s in ("", "---", "--", "-", "null", "None", "NaN", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def sesion_http(user_agent="servidor-meteo-escuelas/1.0"):
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    return s
