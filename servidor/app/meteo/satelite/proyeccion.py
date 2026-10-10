"""
Proyección geoestacionaria del GOES (la "grilla fija" del instrumento ABI).

El satélite no ve el mundo en latitud/longitud: ve ÁNGULOS. Cada píxel de una
imagen ABI se identifica por dos ángulos de escaneo (x, y), en radianes, medidos
desde el satélite. Para recortar nuestra región tenemos que hacer la cuenta
inversa: pasar de (lat, lon) a (x, y) y buscar qué filas/columnas del archivo
caen adentro.

Las fórmulas son las de la "GOES-R Product User Guide" (PUG), vol. 3,
sección 4.2.8 "Navigation of Image Data". Están escritas paso a paso a propósito,
para que se puedan seguir con el papel al lado.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ParametrosGeo:
    """Constantes de la proyección. Vienen en la variable
    `goes_imager_projection` de cada archivo NetCDF."""

    h_satelite: float  # perspective_point_height: altura sobre el ecuador (m)
    r_ecuatorial: float  # semi_major_axis (m)
    r_polar: float  # semi_minor_axis (m)
    lon_origen: float  # longitude_of_projection_origin (grados): -75 para GOES-Este

    @classmethod
    def desde_atributos(cls, attrs):
        return cls(
            h_satelite=float(attrs["perspective_point_height"]),
            r_ecuatorial=float(attrs["semi_major_axis"]),
            r_polar=float(attrs["semi_minor_axis"]),
            lon_origen=float(attrs["longitude_of_projection_origin"]),
        )

    @property
    def H(self):
        """Distancia del satélite al CENTRO de la Tierra."""
        return self.h_satelite + self.r_ecuatorial


# Valores nominales de GOES-16/19 en posición Este; sirven para los tests y como
# referencia. El código real siempre lee los del archivo.
GOES_ESTE = ParametrosGeo(35786023.0, 6378137.0, 6356752.31414, -75.0)


def latlon_a_xy(lat, lon, p: ParametrosGeo):
    """(lat, lon) en grados -> (x, y) ángulos de escaneo en radianes.

    Devuelve NaN para los puntos que el satélite no ve (del otro lado de la
    Tierra).
    """
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    lon0 = np.radians(p.lon_origen)
    req, rpol, H = p.r_ecuatorial, p.r_polar, p.H

    # 1) Latitud geocéntrica: la Tierra es un elipsoide, no una esfera.
    lat_c = np.arctan((rpol**2 / req**2) * np.tan(lat))
    # 2) Distancia del centro de la Tierra al punto de la superficie.
    e2 = (req**2 - rpol**2) / req**2
    rc = rpol / np.sqrt(1 - e2 * np.cos(lat_c) ** 2)
    # 3) Vector satélite -> punto, en coordenadas centradas en el satélite.
    sx = H - rc * np.cos(lat_c) * np.cos(lon - lon0)
    sy = -rc * np.cos(lat_c) * np.sin(lon - lon0)
    sz = rc * np.sin(lat_c)
    # 4) Ángulos que ese vector forma con el eje satélite-centro de la Tierra.
    x = np.arcsin(-sy / np.sqrt(sx**2 + sy**2 + sz**2))
    y = np.arctan(sz / sx)

    # Visibilidad: el punto está del lado de la Tierra que mira el satélite.
    visible = H * (H - sx) >= sy**2 + (req**2 / rpol**2) * sz**2
    x = np.where(visible, x, np.nan)
    y = np.where(visible, y, np.nan)
    return x, y


def xy_a_latlon(x, y, p: ParametrosGeo):
    """Cuenta inversa: (x, y) en radianes -> (lat, lon) en grados.
    NaN donde el ángulo apunta al espacio (fuera del disco terrestre)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    req, rpol, H = p.r_ecuatorial, p.r_polar, p.H
    lon0 = np.radians(p.lon_origen)

    a = np.sin(x) ** 2 + np.cos(x) ** 2 * (np.cos(y) ** 2 + (req**2 / rpol**2) * np.sin(y) ** 2)
    b = -2 * H * np.cos(x) * np.cos(y)
    c = H**2 - req**2
    disc = b**2 - 4 * a * c
    with np.errstate(invalid="ignore"):
        rs = (-b - np.sqrt(disc)) / (2 * a)
    sx = rs * np.cos(x) * np.cos(y)
    sy = -rs * np.sin(x)
    sz = rs * np.cos(x) * np.sin(y)
    lat = np.arctan((req**2 / rpol**2) * (sz / np.sqrt((H - sx) ** 2 + sy**2)))
    lon = lon0 - np.arctan(sy / (H - sx))
    lat = np.where(disc >= 0, np.degrees(lat), np.nan)
    lon = np.where(disc >= 0, np.degrees(lon), np.nan)
    return lat, lon


def ventana_de_recorte(x_coord, y_coord, region, p: ParametrosGeo, margen_px=4):
    """Qué filas y columnas del archivo cubren una región lat/lon.

    region = (lon_min, lon_max, lat_min, lat_max).
    x_coord / y_coord = vectores de ángulos del archivo (x crece hacia el este,
    y DECRECE hacia el sur: la fila 0 es el norte).

    Se proyecta todo el BORDE del rectángulo (no solo las 4 esquinas) porque en
    esta proyección los lados rectos en lat/lon se ven curvos.

    Devuelve (fila_ini, fila_fin, col_ini, col_fin), con fin exclusivo, listo
    para usar como datos[fila_ini:fila_fin, col_ini:col_fin].
    """
    lon_min, lon_max, lat_min, lat_max = region
    n = 50
    lons = np.concatenate([
        np.linspace(lon_min, lon_max, n), np.full(n, lon_max),
        np.linspace(lon_max, lon_min, n), np.full(n, lon_min),
    ])
    lats = np.concatenate([
        np.full(n, lat_min), np.linspace(lat_min, lat_max, n),
        np.full(n, lat_max), np.linspace(lat_max, lat_min, n),
    ])
    x, y = latlon_a_xy(lats, lons, p)
    if np.all(np.isnan(x)):
        raise ValueError(f"La región {region} no es visible desde este satélite")
    x_min, x_max = np.nanmin(x), np.nanmax(x)
    y_min, y_max = np.nanmin(y), np.nanmax(y)

    x_coord = np.asarray(x_coord)
    y_coord = np.asarray(y_coord)
    # x es creciente: searchsorted directo.
    c0 = int(np.searchsorted(x_coord, x_min, side="left")) - margen_px
    c1 = int(np.searchsorted(x_coord, x_max, side="right")) + margen_px
    # y es decreciente: se busca sobre -y, que sí es creciente.
    f0 = int(np.searchsorted(-y_coord, -y_max, side="left")) - margen_px
    f1 = int(np.searchsorted(-y_coord, -y_min, side="right")) + margen_px

    f0, c0 = max(f0, 0), max(c0, 0)
    f1, c1 = min(f1, len(y_coord)), min(c1, len(x_coord))
    return f0, f1, c0, c1
