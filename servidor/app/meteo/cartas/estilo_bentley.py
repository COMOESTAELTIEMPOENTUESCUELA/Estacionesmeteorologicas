"""
Pronóstico del GFS dibujado en el servidor con el MISMO formato que las
cartas de Alicia Bentley (Universidad de Albany): misma región, proyección,
campos, intervalos y colores. Así no dependemos de que su página ande (el
28/10/2026 publicó la misma imagen vieja en todos los cuadros).

Los colores de cada escala se leyeron de las barras de sus imágenes (píxel por
píxel). Lo que NO se copia es su firma: las nuestras dicen de dónde salen.

Las cuatro cartas (una por plazo, de +0 a +192 h cada 6 h):

  mslp_jet     Presión a nivel del mar, espesor 1000-500 hPa y viento en 250 hPa
  850_thetae   Altura de 850 hPa, θe y viento
  ivt          Altura de 700 hPa y transporte integrado de vapor (IVT)
  500_vort     Altura y temperatura en 500 hPa, ascenso y vorticidad ciclónica

Los datos: los mismos archivos del GFS que usa diagnostico.py, pero de cada
plazo de pronóstico (f000 ... f192) en vez de solo el análisis.
"""
import io
from datetime import timedelta

import matplotlib

matplotlib.use("Agg")
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402

from .diagnostico import KT, NIVELES_IVT, ivt, suavizar, theta_e, vorticidad_relativa  # noqa: E402

# Región de las cartas de Bentley ("southamer"), en Mercator. Medida sobre sus
# imágenes: 20° de longitud = 143 píxeles; el ecuador, 87 píxeles bajo el borde.
EXTENSION = (-122.5, 12, -67.3, 12)
# Lo que se baja (con un margen alrededor, para que no queden bordes vacíos).
REGION = (-125, 15, -70, 15)
PLAZOS = list(range(0, 193, 6))         # +0 (análisis) a +192 h, cada 6 h

PEDIDOS = ({("PRMSL", "mean sea level"), ("PRES", "surface"),
            ("HGT", "1000 mb"), ("HGT", "850 mb"), ("HGT", "700 mb"), ("HGT", "500 mb"),
            ("UGRD", "250 mb"), ("VGRD", "250 mb"), ("TMP", "850 mb"), ("RH", "850 mb"),
            ("TMP", "500 mb"), ("ABSV", "500 mb"), ("VVEL", "500 mb")}
           | {(v, f"{n} mb") for v in ("SPFH", "UGRD", "VGRD") for n in NIVELES_IVT})

CARTAS = {
    "mslp_jet": "Presión, espesor 1000/500 hPa y corriente en chorro",
    "850_thetae": "Temperatura potencial equivalente en 850 hPa",
    "ivt": "Transporte integrado de vapor (IVT) y altura de 700 hPa",
    "500_vort": "Vorticidad, temperatura y ascenso en 500 hPa",
}

# ---------------------------------------------------------------------------
# Escalas de colores (de las barras de Bentley). Cada lista de colores tiene
# uno más que los límites: el primero es "menos que el primer límite" y el
# último "más que el último".
# ---------------------------------------------------------------------------
VIENTO_250 = ([30, 40, 50, 60, 70, 80, 90, 100, 110],
              ["none", "#9bdafb", "#56bef8", "#5491f5", "#f3aebf", "#f3839e", "#ec5377", "#f22858",
               "#c8c8c8", "#e7e7e7"])
THETA_E = (list(range(250, 371, 6)),
           ["#ffffff", "#0101c3", "#0129ff", "#0051fe", "#0099fe", "#01ccff", "#01f5e2", "#03e19f",
            "#06c549", "#27bc0c", "#88db07", "#ffe101", "#ffbb01", "#ff8b01", "#ff5700", "#eb0010",
            "#b4003d", "#7f0167", "#9a66b7", "#c4a3d4", "#ddcbe6", "#f0edf3"])
IVT = ([250, 300, 400, 500, 600, 700, 800, 1000, 1200, 1400, 1600],
       ["none", "#fff687", "#ffe131", "#ffc616", "#feaa00", "#fe8d00", "#ff6e00", "#ff4400", "#d01a1a",
        "#a0a0a0", "#bebebe", "#d2d2d2"])
VORT_CICLONICA = ([-28, -24, -20, -16, -12, -10, -8, -6],
                  ["#c81d13", "#e6281d", "#ff5500", "#ff6f33", "#ff9a33", "#ffaf34", "#fecc32", "#ffe770",
                   "none"])

TIERRA = "#d3d3d3"
PC = ccrs.PlateCarree()

# Tamaño y lugares (en píxeles de una imagen de 1160 x 912, como las de ella).
ANCHO_PX, ALTO_PX = 1160, 912
MAPA_PX = (100, 48, 1060, 790)          # izquierda, arriba, derecha, abajo
BARRA_PX = (2, 856, 1158, 883)


def _caja(px):
    x0, y0, x1, y1 = px
    return [x0 / ANCHO_PX, 1 - y1 / ALTO_PX, (x1 - x0) / ANCHO_PX, (y1 - y0) / ALTO_PX]


def _escala(limites, colores):
    """(cmap, norm) con un color por intervalo, incluidos "menos que" y "más que"."""
    cmap = ListedColormap(colores[1:-1])
    cmap.set_under(colores[0])
    cmap.set_over(colores[-1])
    return cmap, BoundaryNorm(limites, cmap.N)


def _marco(titulo, inicio, plazo, tierra=True, costa="black", titulo2_der=""):
    fig = plt.figure(figsize=(ANCHO_PX / 100, ALTO_PX / 100), dpi=100)
    ax = fig.add_axes(_caja(MAPA_PX), projection=ccrs.Mercator())
    ax.set_extent(EXTENSION, crs=PC)
    if tierra:
        ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor=TIERRA, zorder=0)
    ancho = 1.3 if costa != "black" else 0.7
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), linewidth=ancho, edgecolor=costa, zorder=3)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), linewidth=ancho * 0.6, edgecolor=costa, zorder=3)
    gl = ax.gridlines(crs=PC, draw_labels=True, xlocs=range(-120, 21, 20), ylocs=range(-80, 21, 20),
                      linewidth=0.9, color="#8c8c8c", linestyle=(0, (6, 4)), zorder=6)
    gl.top_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 16}
    valida = inicio + timedelta(hours=plazo)
    fig.text(100 / ANCHO_PX, 1 - 17 / ALTO_PX, titulo, fontsize=11.5, va="center")
    fig.text(100 / ANCHO_PX, 1 - 38 / ALTO_PX,
             f"Initialized: {inicio:%H%M} UTC {inicio:%d %b %Y} | Forecast hour: {plazo} | "
             f"Valid: {valida:%H%M} UTC {valida:%d %b %Y}", fontsize=9.5, va="center")
    if titulo2_der:
        fig.text(1060 / ANCHO_PX, 1 - 38 / ALTO_PX, titulo2_der, fontsize=11.5, va="center", ha="right")
    ax.text(0.008, 0.012, "GFS 0,5° (NOAA)\nprocesado en el servidor del proyecto", transform=ax.transAxes,
            fontsize=8.5, zorder=7, bbox={"facecolor": "white", "alpha": 0.6, "pad": 1.5, "linewidth": 0})
    return fig, ax


def _barra(fig, mapeo, limites):
    cax = fig.add_axes(_caja(BARRA_PX))
    cb = fig.colorbar(mapeo, cax=cax, orientation="horizontal", ticks=limites, extend="both",
                      extendfrac=0.105, extendrect=True, drawedges=True)
    cb.ax.tick_params(labelsize=16, length=0, pad=6)
    cb.outline.set_linewidth(0.8)
    cb.dividers.set_linewidth(0.8)


def _sombreado(ax, lon, lat, campo, escala, **kw):
    limites, colores = escala
    cmap, norm = _escala(limites, colores)
    return ax.contourf(lon, lat, campo, levels=limites, cmap=cmap, norm=norm, extend="both",
                       transform=PC, zorder=1, **kw)


def _contornos(ax, lon, lat, campo, niveles, color="black", ancho=1.3, estilo="solid", etiquetas=True,
               cada=1, tam=10):
    cs = ax.contour(lon, lat, campo, levels=niveles, colors=color, linewidths=ancho,
                    linestyles=[estilo] if not isinstance(estilo, str) else estilo, transform=PC, zorder=4)
    if etiquetas:
        ax.clabel(cs, cs.levels[::cada], fmt="%d", fontsize=tam, inline=True, inline_spacing=3)
    return cs


def _barbas(ax, lon, lat, u, v, cada=9):
    s = (slice(cada // 2, None, cada), slice(cada // 2, None, cada))
    ax.barbs(lon[s], lat[s], u[s] * KT, v[s] * KT, length=5.2, linewidth=0.7, color="#3a3a3a",
             transform=PC, zorder=5)


def _png(fig):
    """PNG con paleta de 256 colores: estas cartas tienen pocos colores (las
    escalas tienen 10 a 22) y pesa ~3 veces menos (~180 KB en vez de ~580)
    sin que se note la diferencia."""
    from PIL import Image
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    salida = io.BytesIO()
    img.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(salida, "PNG", optimize=True)
    return salida.getvalue()


# Latitudes de la tabla del marco (ver marco()).
LATS_TABLA = np.arange(-80, 84.01, 0.5)


def marco():
    """Dónde está el mapa dentro de la imagen y qué cubre, para que la página
    pueda poner puntos (ciudades) o decir la posición del cursor:
        caja: [izquierda, arriba, derecha, abajo] en fracción de la imagen
        x, y: límites del mapa en metros de la proyección
        mx:   metros por grado de longitud (la x es lineal)
        lat, my: tabla latitud -> y (metros), cada 0,5°. La y NO es la
              fórmula de libro de la Mercator: cartopy convierte la latitud
              antes de proyectar (~0,6 % de diferencia, 2 píxeles). Con la
              tabla, sacada de cartopy, coincide con lo dibujado."""
    from datetime import datetime, timezone
    fig, ax = _marco("", datetime(2026, 1, 1, tzinfo=timezone.utc), 0)
    fig.canvas.draw()                  # recién al dibujar se ajusta la caja a la proporción del mapa
    b = ax.get_position()
    puntos = ax.projection.transform_points(PC, np.zeros_like(LATS_TABLA), LATS_TABLA)
    mx = ax.projection.transform_point(1, 0, PC)[0]
    salida = {"caja": [round(b.x0, 6), round(1 - b.y1, 6), round(b.x1, 6), round(1 - b.y0, 6)],
              "x": [float(v) for v in ax.get_xlim()], "y": [float(v) for v in ax.get_ylim()],
              "mx": float(mx), "lat": [float(v) for v in LATS_TABLA], "my": [round(float(v), 1) for v in puntos[:, 1]]}
    plt.close(fig)
    return salida


def a_imagen(m, lon, lat):
    """(lon, lat) -> (fracción x, fracción y desde arriba) de la imagen, con el
    marco m (la misma cuenta que hace la página en JavaScript)."""
    x, y = lon * m["mx"], np.interp(lat, m["lat"], m["my"])
    (x0, x1), (y0, y1) = m["x"], m["y"]
    c = m["caja"]
    return (c[0] + (x - x0) / (x1 - x0) * (c[2] - c[0]),
            c[3] - (y - y0) / (y1 - y0) * (c[3] - c[1]))


TRAZOS = (0, (5, 3))


def dibujar(campos, inicio, plazo):
    """{(variable, nivel): Campo} de un plazo -> {id de carta: PNG en bytes}."""
    c = lambda var, nivel: campos[(var, nivel)].valores
    ref = campos[("PRMSL", "mean sea level")]
    lon, lat = np.meshgrid(ref.lon, ref.lat)
    salida = {}

    # 1) Presión, espesor y chorro
    fig, ax = _marco("MSLP (black, hPa), 1000-500-hPa thickness (red/blue, dam), 250-hPa wind speed (shaded, m/s)",
                     inicio, plazo)
    vel = np.hypot(c("UGRD", "250 mb"), c("VGRD", "250 mb"))
    m = _sombreado(ax, lon, lat, vel, VIENTO_250)
    espesor = suavizar((c("HGT", "500 mb") - c("HGT", "1000 mb")) / 10, pasadas=2)
    _contornos(ax, lon, lat, espesor, np.arange(546, 601, 6), "#ee1111", 1.3, TRAZOS, cada=1)
    _contornos(ax, lon, lat, espesor, np.arange(480, 541, 6), "#1111cc", 1.3, TRAZOS, cada=1)
    _contornos(ax, lon, lat, suavizar(c("PRMSL", "mean sea level") / 100, pasadas=3), np.arange(900, 1081, 4))
    _barra(fig, m, VIENTO_250[0])
    salida["mslp_jet"] = _png(fig)

    # 2) θe en 850 hPa (sin tierra gris: el sombreado cubre todo; costas azul oscuro)
    fig, ax = _marco("850-hPa geo. height (black, dam), equivalent potential temp. (shaded, K), wind (barbs, kt)",
                     inicio, plazo, tierra=False, costa="#00008b")
    te = theta_e(c("TMP", "850 mb"), c("RH", "850 mb"), 850.0)
    m = _sombreado(ax, lon, lat, te, THETA_E)
    _contornos(ax, lon, lat, suavizar(c("HGT", "850 mb") / 10, 1), np.arange(60, 181, 3), cada=2)
    _barbas(ax, lon, lat, c("UGRD", "850 mb"), c("VGRD", "850 mb"))
    _barra(fig, m, THETA_E[0][::2])
    salida["850_thetae"] = _png(fig)

    # 3) IVT y altura de 700 hPa
    fig, ax = _marco("700-hPa geo. height (black, dam), Integrated water vapor transport [IVT] (shaded, kg/m/s) "
                     "(vectors, kg/m/s)", inicio, plazo)
    q = np.stack([c("SPFH", f"{n} mb") for n in NIVELES_IVT])
    u = np.stack([c("UGRD", f"{n} mb") for n in NIVELES_IVT])
    v = np.stack([c("VGRD", f"{n} mb") for n in NIVELES_IVT])
    iu, iv = ivt(q, u, v, NIVELES_IVT, c("PRES", "surface") / 100)
    mod = np.hypot(iu, iv)
    m = _sombreado(ax, lon, lat, mod, IVT)
    ax.contour(lon, lat, mod, levels=IVT[0], colors="#b36b00", linewidths=0.5, transform=PC, zorder=2)
    _contornos(ax, lon, lat, suavizar(c("HGT", "700 mb") / 10, 1), np.arange(200, 340, 3), cada=2)
    s = (slice(4, None, 9), slice(4, None, 9))
    ax.quiver(lon[s], lat[s], iu[s], iv[s], scale=1000 / 0.045, scale_units="width", width=0.0032,
                        headwidth=3.2, headlength=3.6, headaxislength=3.2, minlength=0.4, color="black",
                        transform=PC, zorder=5, regrid_shape=None)
    # Recuadro con la flecha de referencia (abajo a la derecha, como en las de ella).
    ax.add_patch(plt.Rectangle((0.845, 0.035), 0.15, 0.13, transform=ax.transAxes, facecolor="white",
                               edgecolor="black", linewidth=0.8, zorder=7))
    ax.text(0.92, 0.125, "1000 kg m$^{-1}$ s$^{-1}$", ha="center", va="center", fontsize=11,
            transform=ax.transAxes, zorder=8)
    # La flecha de 1000 mide lo mismo que en el mapa: scale = 1000 / 0,045 del ancho.
    ax.annotate("", xy=(0.92 + 0.0225, 0.07), xytext=(0.92 - 0.0225, 0.07), xycoords="axes fraction",
                arrowprops={"arrowstyle": "-|>", "lw": 2.4, "color": "black", "mutation_scale": 14}, zorder=8)
    _barra(fig, m, IVT[0])
    salida["ivt"] = _png(fig)

    # 4) 500 hPa: altura, temperatura, ascenso y vorticidad ciclónica (negativa en el hemisferio sur)
    fig, ax = _marco("500-hPa geo. height (black, dam), temp. (red, C), ascent (blue, 5 x 10^-3 hPa/s), "
                     "cyc. rel. vort. (x 10^-5 s^-1),", inicio, plazo, titulo2_der="wind (barbs, kt)")
    zeta = suavizar(vorticidad_relativa(c("ABSV", "500 mb"), ref.lat) * 1e5, pasadas=1)
    m = _sombreado(ax, lon, lat, zeta, VORT_CICLONICA)
    # Ascenso = omega NEGATIVO. VVEL viene en Pa/s: cada 5 x 10⁻³ hPa/s = 0,5 Pa/s.
    omega = c("VVEL", "500 mb")
    _contornos(ax, lon, lat, omega, np.arange(-10, -0.4, 0.5), "#1515ff", 1.1, etiquetas=False)
    _contornos(ax, lon, lat, suavizar(c("TMP", "500 mb") - 273.15, 2), np.arange(-60, 1, 4), "#ee1111", 1.3,
               TRAZOS, tam=9)
    _contornos(ax, lon, lat, suavizar(c("HGT", "500 mb") / 10, 1), np.arange(450, 621, 6), cada=2)
    _barbas(ax, lon, lat, c("UGRD", "500 mb"), c("VGRD", "500 mb"))
    _barra(fig, m, VORT_CICLONICA[0])
    salida["500_vort"] = _png(fig)
    return salida
