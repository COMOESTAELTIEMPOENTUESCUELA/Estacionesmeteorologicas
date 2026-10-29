"""
Análisis del GFS (el modelo global de NOAA) para dibujar nuestras propias
cartas de diagnóstico, sin depender de páginas ajenas.

De dónde salen (gratis, en el bucket público de NOAA en Amazon, con historia
desde 2021):

    https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261026/12/atmos/gfs.t12z.pgrb2.0p50.f000

- f000 = el ANÁLISIS (hora 0 del pronóstico): el estado de la atmósfera a esa
  hora según el modelo con todas las observaciones asimiladas.
- 0p50 = grilla de 0,5° (la que usaban las cartas de Bentley).
- Formato GRIB2: muchos "mensajes" seguidos, uno por variable y nivel.

El archivo entero pesa ~120 MB, pero no hace falta bajarlo: al lado hay un
índice (.idx) de texto que dice en qué byte empieza cada mensaje:

    461:97419357:d=2026102612:HGT:850 mb:anl:
    462:97686933:d=2026102612:TMP:850 mb:anl:

Con eso se piden solo los mensajes que usamos (encabezado HTTP "Range", como
hace el módulo de satélite): ~16 MB por horario en vez de 120.
Los mensajes se decodifican con eccodes (la librería del ECMWF para GRIB).
"""
from dataclasses import dataclass
from datetime import timezone

import numpy as np

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"


@dataclass
class Campo:
    lat: np.ndarray        # 1D, de norte a sur
    lon: np.ndarray        # 1D, -180..180
    valores: np.ndarray    # 2D (lat, lon)


def urls(ts, resolucion="0p50", plazo=0):
    """Direcciones posibles del archivo de la corrida ts (NOAA agregó la carpeta
    "atmos" en 2021). plazo=0 es el análisis; 6, 12... los pronósticos."""
    ts = ts.astimezone(timezone.utc)
    nombre = f"gfs.t{ts:%H}z.pgrb2.{resolucion}.f{plazo:03d}"
    return [f"{BASE}/gfs.{ts:%Y%m%d}/{ts:%H}/atmos/{nombre}", f"{BASE}/gfs.{ts:%Y%m%d}/{ts:%H}/{nombre}"]


def leer_indice(texto):
    """Texto del .idx -> {(variable, nivel): (byte inicial, byte final o None)}."""
    filas = []
    for linea in texto.splitlines():
        partes = linea.split(":")
        if len(partes) >= 6:
            filas.append((int(partes[1]), partes[3], partes[4]))
    indice = {}
    for i, (inicio, var, nivel) in enumerate(filas):
        fin = filas[i + 1][0] - 1 if i + 1 < len(filas) else None
        indice.setdefault((var, nivel), (inicio, fin))   # si se repite, el primero
    return indice


def tramos(indice, pedidos):
    """Rangos de bytes a pedir, juntando los mensajes que están seguidos en el
    archivo (menos pedidos HTTP). -> [(inicio, fin, [(clave, inicio, fin)])]"""
    faltan = [k for k in pedidos if k not in indice]
    if faltan:
        raise KeyError(f"el análisis no trae: {faltan[:5]}")
    partes = sorted((indice[k][0], indice[k][1], k) for k in pedidos)
    grupos = []
    for ini, fin, k in partes:
        if grupos and grupos[-1][1] is not None and ini == grupos[-1][1] + 1:
            grupos[-1][1] = fin
            grupos[-1][2].append((k, ini, fin))
        else:
            grupos.append([ini, fin, [(k, ini, fin)]])
    return [tuple(g) for g in grupos]


# Margen alrededor de la región (grados): la proyección cónica de las cartas
# muestra más que el rectángulo de latitud/longitud pedido.
MARGEN_LON, MARGEN_LAT = 25, 10


def decodificar(mensaje, region):
    """Un mensaje GRIB2 (bytes) -> Campo recortado a la región (con margen)."""
    import eccodes
    gid = eccodes.codes_new_from_message(mensaje)
    try:
        ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
        lat0 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lon0 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        dlat = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
        dlon = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
        sube = eccodes.codes_get(gid, "jScansPositively")
        valores = eccodes.codes_get_values(gid).reshape(nj, ni)
    finally:
        eccodes.codes_release(gid)
    lat = lat0 + (dlat if sube else -dlat) * np.arange(nj)
    lon = (lon0 + dlon * np.arange(ni) + 180) % 360 - 180      # 0..360 -> -180..180
    if sube:
        lat, valores = lat[::-1], valores[::-1]
    orden = np.argsort(lon)
    lon, valores = lon[orden], valores[:, orden]
    lon_min, lon_max, lat_min, lat_max = region
    fl = (lat >= lat_min - MARGEN_LAT) & (lat <= lat_max + MARGEN_LAT)
    cl = (lon >= lon_min - MARGEN_LON) & (lon <= lon_max + MARGEN_LON)
    return Campo(lat[fl], lon[cl], valores[np.ix_(fl, cl)])


def bajar(ts, pedidos, region, sesion, resolucion="0p50", plazo=0):
    """{(variable, nivel): Campo} de la corrida ts (plazo=0: el análisis).
    FileNotFoundError si todavía no está publicado (el análisis sale ~3,5-4 h
    después de la hora; el +192 h, ~5 h)."""
    for url in urls(ts, resolucion, plazo):
        r = sesion.get(url + ".idx", timeout=60)
        if r.status_code == 200:
            break
    else:
        raise FileNotFoundError(f"el GFS de {ts:%d/%m %H} UTC (+{plazo} h) todavía no está publicado")
    indice = leer_indice(r.text)
    campos = {}
    for ini, fin, partes in tramos(indice, pedidos):
        rango = f"bytes={ini}-{'' if fin is None else fin}"
        datos = sesion.get(url, headers={"Range": rango}, timeout=120)
        datos.raise_for_status()
        for clave, a, b in partes:
            trozo = datos.content[a - ini: None if b is None else b - ini + 1]
            campos[clave] = decodificar(trozo, region)
    return campos
