"""
Imágenes del satélite GOES-19 ya coloreadas que publica NOAA STAR, para un
sector (ej. "ssa": sur de Sudamérica):

    https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/13/

En esa carpeta, además de la "más reciente" (1800x1080.jpg), está cada imagen
de los últimos días con la fecha y hora en el nombre (año, día del año, hora
y minuto, en UTC):

    20262861200_GOES19-ABI-ssa-13-1800x1080.jpg   ->  2026, día 286, 12:00

Para tener la de una hora sinóptica exacta (y no "la última que había cuando
se corrió la tarea") se lee la lista y se elige la más cercana a esa hora.
Gratis: NOAA publica estos datos abiertos.
"""
import re
from datetime import datetime, timedelta, timezone

BASE = "https://cdn.star.nesdis.noaa.gov/{satelite}/ABI/SECTOR/{sector}/{banda}/"
_ARCHIVO = re.compile(r'(\d{4})(\d{3})(\d{2})(\d{2})_(GOES\d+)-ABI-([a-z]+)-([A-Za-z0-9]+)-(\d+x\d+)\.jpg')
TOLERANCIA = timedelta(minutes=10)


def url_carpeta(p):
    return BASE.format(satelite=p.get("satelite", "GOES19"), sector=p["sector"], banda=p["banda"])


def imagenes(html, tamano="1800x1080"):
    """Lista de la carpeta -> {ts: nombre de archivo} (solo el tamaño pedido)."""
    salida = {}
    for m in _ARCHIVO.finditer(html):
        anio, dia, hora, minuto, _sat, _sector, _banda, tam = m.groups()
        if tam != tamano:
            continue
        ts = (datetime(int(anio), 1, 1, tzinfo=timezone.utc)
              + timedelta(days=int(dia) - 1, hours=int(hora), minutes=int(minuto)))
        salida[ts] = m.group(0)
    return salida


def elegir(disponibles, ts):
    """La imagen más cercana a ts, si está a menos de TOLERANCIA; si no, None."""
    if not disponibles:
        return None
    mejor = min(disponibles, key=lambda t: abs(t - ts))
    return disponibles[mejor] if abs(mejor - ts) <= TOLERANCIA else None
