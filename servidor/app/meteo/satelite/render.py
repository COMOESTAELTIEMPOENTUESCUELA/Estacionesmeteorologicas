"""
Dibujar un recorte como imagen PNG con mapa.

Paletas:
- ir_realzada: infrarrojo "realzado". Lo cálido (suelo, nubes bajas) en grises;
  por debajo de -30 °C colores, para resaltar topes fríos = nubes altas y
  convección profunda. Es la convención que usan el SMN y NOAA.
- vapor: vapor de agua. Blanco/verde/azul = capa media húmeda; naranja/marrón =
  aire seco (temperatura de brillo alta porque el satélite "ve" más abajo).
- visible: reflectancia 0-1 en grises con raíz cuadrada (corrección gamma
  sencilla), para que se vean las nubes finas sin quemar las gruesas.
"""
from datetime import timedelta

import matplotlib

matplotlib.use("Agg")  # sin pantalla: dibujar directo a archivo
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402

from .procesar import BANDAS  # noqa: E402
from .proyeccion import xy_a_latlon  # noqa: E402

HORA_ARGENTINA = timedelta(hours=-3)


def _cmap_ir():
    # (temperatura °C, color), de lo más frío a lo más cálido.
    tmin, tmax = -90.0, 50.0
    paradas = [
        (-90, "#ffffff"), (-85, "#ff00ff"),  # topes muy fríos: blanco-magenta
        (-80, "#8b0000"), (-75, "#ff0000"),  # bordo-rojo
        (-70, "#ff8000"), (-60, "#ffff00"),  # naranja-amarillo
        (-50, "#00c800"), (-40, "#0050ff"),  # verde-azul
        (-31, "#00e5ff"),                    # celeste
        (-30, "#d9d9d9"), (50, "#000000"),   # gris: nubes bajas y suelo (cálido = oscuro)
    ]
    pos = [(t - tmin) / (tmax - tmin) for t, _ in paradas]
    cmap = LinearSegmentedColormap.from_list("ir_realzada", list(zip(pos, [c for _, c in paradas])))
    return cmap, Normalize(vmin=tmin, vmax=tmax, clip=True), "Temperatura de brillo (°C)"


def _cmap_vapor():
    tmin, tmax = -75.0, -5.0
    paradas = [
        (-75, "#ffffff"), (-55, "#7fd47f"), (-45, "#2a6fdb"),
        (-35, "#1a1a1a"), (-25, "#e08a2e"), (-5, "#5a2d0c"),
    ]
    pos = [(t - tmin) / (tmax - tmin) for t, _ in paradas]
    cmap = LinearSegmentedColormap.from_list("vapor", list(zip(pos, [c for _, c in paradas])))
    return cmap, Normalize(vmin=tmin, vmax=tmax, clip=True), "Temperatura de brillo (°C)"


PALETAS = {"ir_realzada": _cmap_ir, "vapor": _cmap_vapor}


def dibujar(rec, ruta_png, region, paleta, titulo_region="", puntos=(), dpi=110):
    """rec: Recorte de procesar.recortar(). region: (lon_min, lon_max, lat_min, lat_max)."""
    # Latitud y longitud de cada píxel, con las mismas fórmulas del recorte
    # (verificadas contra pyproj en los tests). Así dibujamos cada píxel en su
    # lugar real, sin depender de la reproyección interna de cartopy.
    xx, yy = np.meshgrid(rec.x, rec.y)
    lat, lon = xy_a_latlon(xx, yy, rec.geo)

    if paleta == "visible":
        valores = np.sqrt(np.clip(rec.datos, 0, 1))
        cmap, norm, etiqueta = plt.get_cmap("gray"), Normalize(0, 1), "Reflectancia (escala raíz)"
    else:
        valores = rec.datos - 273.15  # K -> °C
        cmap, norm, etiqueta = PALETAS[paleta]()

    alto_ancho = (region[3] - region[2]) / (region[1] - region[0])
    fig = plt.figure(figsize=(8, 8 * alto_ancho + 1.0))
    ax = fig.add_axes([0.07, 0.1, 0.91, 0.84], projection=ccrs.PlateCarree())
    ax.set_extent(region, crs=ccrs.PlateCarree())
    ax.set_facecolor("black")

    im = ax.pcolormesh(lon, lat, np.ma.masked_invalid(valores), cmap=cmap, norm=norm,
                       shading="nearest", transform=ccrs.PlateCarree(), rasterized=True)

    color_linea = "#ffd400" if paleta == "visible" else "#202020"
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), edgecolor=color_linea, linewidth=0.7)
    ax.add_feature(cfeature.BORDERS.with_scale("10m"), edgecolor=color_linea, linewidth=0.7)
    provincias = cfeature.NaturalEarthFeature("cultural", "admin_1_states_provinces_lines", "10m", facecolor="none")
    ax.add_feature(provincias, edgecolor=color_linea, linewidth=0.4)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="#888888", alpha=0.6, linestyle="--")
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 7}

    for p in puntos:
        ax.plot(p["lon"], p["lat"], marker="o", markersize=3.5, color="#ff2d55",
                markeredgecolor="white", markeredgewidth=0.6, transform=ccrs.PlateCarree())
        if p.get("etiqueta"):
            ax.text(p["lon"] + p.get("dx", 0.06), p["lat"] + p.get("dy", 0.06), p["etiqueta"],
                    fontsize=6.5, color="white",
                    transform=ccrs.PlateCarree(),
                    bbox={"facecolor": "black", "alpha": 0.45, "pad": 1, "linewidth": 0})

    nombre, onda = BANDAS.get(rec.banda, ("", 0))
    local = rec.inicio + HORA_ARGENTINA
    fig.text(0.02, 0.965, f"GOES-19 · Banda {rec.banda} ({onda} µm) · {nombre}", fontsize=11, weight="bold")
    fig.text(0.02, 0.945, f"{rec.inicio:%d/%m/%Y %H:%M} UTC  ({local:%H:%M} hora Argentina)  ·  {titulo_region}",
             fontsize=8.5)
    cax = fig.add_axes([0.1, 0.055, 0.8, 0.018])
    cb = fig.colorbar(im, cax=cax, orientation="horizontal")
    cb.set_label(etiqueta, fontsize=7.5)
    cb.ax.tick_params(labelsize=7)
    fig.text(0.98, 0.01, "Datos: NOAA/NESDIS (GOES-19 ABI L2 CMIP) · Procesado localmente",
             fontsize=6, ha="right", color="#555555")
    fig.savefig(ruta_png, dpi=dpi)
    plt.close(fig)
