"""
Cartas de diagnóstico propias, a partir del análisis del GFS (ver gfs.py).
Reemplazan a las de Bentley, con los mismos cuatro campos:

  presion_chorro  Presión a nivel del mar, espesor 1000-500 hPa y chorro en 250 hPa
  thetae_850      Temperatura potencial equivalente, altura y viento en 850 hPa
  ivt             Transporte integrado de vapor (IVT) y presión a nivel del mar
  vorticidad_500  Vorticidad relativa, altura y viento en 500 hPa

Las cuatro se dibujan con EXACTAMENTE el mismo marco (proyección, extensión y
tamaño): así funcionan el cursor sincronizado y "superponer" del visualizador.

Las cuentas están escritas paso a paso para poder seguirlas con un libro al
lado (Holton, "An Introduction to Dynamic Meteorology"; Bolton 1980 para la θe).
"""
import io
from datetime import timedelta

import matplotlib

matplotlib.use("Agg")
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import BoundaryNorm  # noqa: E402

G = 9.80665              # gravedad (m/s²)
OMEGA = 7.2921e-5        # velocidad angular de la Tierra (rad/s)
KT = 1.943844            # m/s -> nudos
NIVELES_IVT = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300]
ATM = "entire atmosphere (considered as a single layer)"

# Lo que hay que bajar del GFS para las cuatro cartas (variable, nivel).
PEDIDOS = ({("PRMSL", "mean sea level"), ("PRES", "surface"),
            ("HGT", "1000 mb"), ("HGT", "850 mb"), ("HGT", "500 mb"),
            ("UGRD", "250 mb"), ("VGRD", "250 mb"), ("TMP", "850 mb"), ("RH", "850 mb"),
            ("ABSV", "500 mb")}
           | {(v, f"{n} mb") for v in ("SPFH", "UGRD", "VGRD") for n in NIVELES_IVT})

CARTAS = {
    "presion_chorro": "Presión a nivel del mar, espesor 1000-500 hPa y chorro en 250 hPa",
    "thetae_850": "θe, altura geopotencial y viento en 850 hPa",
    "ivt": "Transporte integrado de vapor (IVT) y presión a nivel del mar",
    "vorticidad_500": "Vorticidad relativa, altura geopotencial y viento en 500 hPa",
}


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------
def theta_e(t_k, hr, p_hpa):
    """Temperatura potencial equivalente (K), fórmula de Bolton (1980).
    t_k: temperatura (K), hr: humedad relativa (%), p_hpa: presión del nivel."""
    tc = t_k - 273.15
    es = 6.112 * np.exp(17.67 * tc / (tc + 243.5))           # presión de saturación (hPa)
    e = np.clip(hr, 1, 100) / 100 * es                         # presión de vapor (hPa)
    r = 0.622 * e / (p_hpa - e)                                # razón de mezcla (kg/kg)
    t_l = 2840 / (3.5 * np.log(t_k) - np.log(e) - 4.805) + 55  # temperatura de condensación (K)
    return (t_k * (1000 / p_hpa) ** (0.2854 * (1 - 0.28 * r))
            * np.exp((3.376 / t_l - 0.00254) * r * 1000 * (1 + 0.81 * r)))


def ivt(q, u, v, p_hpa, p_sup_hpa):
    """Transporte integrado de vapor entre 1000 y 300 hPa (kg m⁻¹ s⁻¹):
        IVT = (1/g) ∫ q·V dp
    q, u, v: (niveles, lat, lon). Los niveles que quedan bajo el suelo (en la
    cordillera, 850 hPa está "adentro" de la montaña) no cuentan."""
    p = np.asarray(p_hpa, float)[:, None, None]
    bajo_suelo = p > p_sup_hpa[None, :, :]
    qu = np.where(bajo_suelo, 0.0, q * u)
    qv = np.where(bajo_suelo, 0.0, q * v)
    orden = np.argsort(p_hpa)                                   # p de menor a mayor
    pa = np.asarray(p_hpa, float)[orden] * 100
    ivt_u = np.trapezoid(qu[orden], pa, axis=0) / G
    ivt_v = np.trapezoid(qv[orden], pa, axis=0) / G
    return ivt_u, ivt_v


def vorticidad_relativa(absoluta, lat):
    """Vorticidad relativa = absoluta - f, con f = 2Ω sen(lat) (parámetro de
    Coriolis). En el hemisferio sur la vorticidad CICLÓNICA es NEGATIVA."""
    f = 2 * OMEGA * np.sin(np.radians(lat))[:, None]
    return absoluta - f


# ---------------------------------------------------------------------------
# Dibujo
# ---------------------------------------------------------------------------
def _proyeccion(region):
    """Cónica conforme de Lambert centrada en la región (la proyección de las
    cartas sinópticas: conserva los ángulos, y las latitudes medias se ven sin
    estirar). `cutoff`: en el hemisferio sur el cono se abre hacia el norte, y
    por defecto cartopy lo corta en 30°S: se lo corre más al norte que la región."""
    lon_c = (region[0] + region[1]) / 2
    lat_c = (region[2] + region[3]) / 2
    return ccrs.LambertConformal(central_longitude=lon_c, central_latitude=lat_c,
                                 standard_parallels=(lat_c + 12, lat_c - 12), cutoff=region[3] + 15)


ANCHO_IN, ARRIBA_IN, ABAJO_IN = 10.4, 0.75, 0.95     # pulgadas: ancho, títulos, escala


def _diseno(region):
    """Tamaño de figura y lugar del mapa: el alto sale de la forma de la región
    en la proyección (sin franjas en blanco). Igual para todas las cartas."""
    proj = _proyeccion(region)
    lon = np.concatenate([np.linspace(region[0], region[1], 50), np.full(50, region[1]),
                          np.linspace(region[1], region[0], 50), np.full(50, region[0])])
    lat = np.concatenate([np.full(50, region[2]), np.linspace(region[2], region[3], 50),
                          np.full(50, region[3]), np.linspace(region[3], region[2], 50)])
    xy = proj.transform_points(ccrs.PlateCarree(), lon, lat)
    aspecto = np.ptp(xy[:, 1]) / np.ptp(xy[:, 0])
    ancho_mapa = ANCHO_IN * 0.92
    alto = ARRIBA_IN + ancho_mapa * aspecto + ABAJO_IN
    return proj, alto, [0.04, ABAJO_IN / alto, 0.92, ancho_mapa * aspecto / alto]


def _marco(region, titulo, ts, subtitulo=""):
    """Figura y mapa idénticos para todas las cartas (mismo tamaño y extensión)."""
    proj, alto, caja = _diseno(region)
    fig = plt.figure(figsize=(ANCHO_IN, alto), dpi=100)
    fig._alto_in = alto
    ax = fig.add_axes(caja, projection=proj)
    ax.set_extent(region, crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f2efe6", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#e3edf5", zorder=0)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), linewidth=0.6, edgecolor="#555555", zorder=3)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), linewidth=0.5, edgecolor="#555555", zorder=3)
    provincias = cfeature.NaturalEarthFeature("cultural", "admin_1_states_provinces_lines", "50m", facecolor="none")
    ax.add_feature(provincias, linewidth=0.25, edgecolor="#888888", zorder=3)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="#999999", alpha=0.6, linestyle="--",
                      x_inline=False, y_inline=False)
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 7}
    local = ts + timedelta(hours=-3)
    fig.text(0.03, 1 - 0.32 / alto, titulo, fontsize=12, weight="bold")
    fig.text(0.03, 1 - 0.55 / alto, f"Análisis GFS 0,5° · {ts:%d/%m/%Y %H} UTC ({local:%H} hs Argentina)"
             + (f" · {subtitulo}" if subtitulo else ""), fontsize=8.5)
    fig.text(0.97, 0.06 / alto, "Datos: NOAA GFS (análisis f000) · Procesado localmente",
             fontsize=6, ha="right", color="#666666")
    return fig, ax


def _barra(fig, mapeo, etiqueta, ticks=None):
    alto = fig._alto_in
    cax = fig.add_axes([0.15, 0.42 / alto, 0.7, 0.14 / alto])
    cb = fig.colorbar(mapeo, cax=cax, orientation="horizontal", ticks=ticks)
    cb.set_label(etiqueta, fontsize=8)
    cb.ax.tick_params(labelsize=7)


def suavizar(campo, pasadas=2):
    """Filtro de 9 puntos (promedio ponderado con los vecinos), repetido. La
    vorticidad a 0,5° es "ruidosa": suavizar deja ver los sistemas (vaguadas,
    bajas) en vez de las manchitas de escala de grilla, como en todas las
    cartas operativas. Los bordes se dejan como están."""
    z = np.array(campo, dtype=float)
    for _ in range(pasadas):
        c = z[1:-1, 1:-1]
        lados = z[:-2, 1:-1] + z[2:, 1:-1] + z[1:-1, :-2] + z[1:-1, 2:]
        esquinas = z[:-2, :-2] + z[:-2, 2:] + z[2:, :-2] + z[2:, 2:]
        nuevo = z.copy()
        nuevo[1:-1, 1:-1] = (4 * c + 2 * lados + esquinas) / 16
        z = nuevo
    return z


def _contornos(ax, lon, lat, campo, niveles, color, ancho=0.9, estilo="solid", formato="%d", tam=7):
    cs = ax.contour(lon, lat, campo, levels=niveles, colors=color, linewidths=ancho, linestyles=estilo,
                    transform=ccrs.PlateCarree(), zorder=4)
    ax.clabel(cs, fmt=formato, fontsize=tam, inline=True, inline_spacing=2)
    return cs


def _barbas(ax, lon, lat, u, v, cada=4):
    s = (slice(None, None, cada), slice(None, None, cada))
    ax.barbs(lon[s], lat[s], u[s] * KT, v[s] * KT, length=4.6, linewidth=0.45, color="#222222",
             transform=ccrs.PlateCarree(), zorder=5)


def _png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def dibujar(campos, ts, region):
    """{(variable, nivel): Campo} -> {id de carta: PNG en bytes}."""
    c = lambda var, nivel: campos[(var, nivel)].valores
    ref = campos[("PRMSL", "mean sea level")]
    lon, lat = np.meshgrid(ref.lon, ref.lat)
    # Suavizado leve: la presión "reducida al nivel del mar" sobre la cordillera
    # (5000 m) es una extrapolación que se enrula; así se ven los sistemas.
    pnm = suavizar(c("PRMSL", "mean sea level") / 100, pasadas=2)
    salida = {}

    # 1) Presión a nivel del mar + espesor 1000-500 + chorro en 250 hPa
    fig, ax = _marco(region, CARTAS["presion_chorro"], ts)
    vel = np.hypot(c("UGRD", "250 mb"), c("VGRD", "250 mb"))
    niveles = [30, 40, 50, 60, 70, 80, 90]
    m = ax.contourf(lon, lat, np.where(vel >= 30, vel, np.nan), levels=niveles, cmap="YlGnBu", extend="max",
                    transform=ccrs.PlateCarree(), zorder=1, alpha=0.85)
    espesor = suavizar((c("HGT", "500 mb") - c("HGT", "1000 mb")) / 10, pasadas=2)   # dam
    frio = np.arange(480, 541, 6)
    calido = np.arange(546, 601, 6)
    _contornos(ax, lon, lat, espesor, frio, "#1f5fbf", 0.8, "dashed")
    _contornos(ax, lon, lat, espesor, calido, "#c0392b", 0.8, "dashed")
    _contornos(ax, lon, lat, pnm, np.arange(940, 1061, 4), "black", 1.0)
    _barra(fig, m, "Viento en 250 hPa (m/s) · espesor 1000-500 hPa (dam, azul ≤ 540, rojo > 540) · presión (hPa)")
    salida["presion_chorro"] = _png(fig)

    # 2) θe, altura y viento en 850 hPa
    fig, ax = _marco(region, CARTAS["thetae_850"], ts)
    te = theta_e(c("TMP", "850 mb"), c("RH", "850 mb"), 850.0)
    te = np.where(c("PRES", "surface") / 100 < 850, np.nan, te)            # bajo el suelo: sin dato
    m = ax.contourf(lon, lat, te, levels=np.arange(276, 365, 4), cmap="turbo", extend="both",
                    transform=ccrs.PlateCarree(), zorder=1, alpha=0.9)
    _contornos(ax, lon, lat, c("HGT", "850 mb") / 10, np.arange(105, 171, 3), "black", 0.9)
    _barbas(ax, lon, lat, c("UGRD", "850 mb"), c("VGRD", "850 mb"))      # 850 y 500 vienen con el IVT
    _barra(fig, m, "θe en 850 hPa (K) · altura (dam) · viento (nudos)")
    salida["thetae_850"] = _png(fig)

    # 3) IVT + presión a nivel del mar
    fig, ax = _marco(region, CARTAS["ivt"], ts)
    q = np.stack([c("SPFH", f"{n} mb") for n in NIVELES_IVT])
    u = np.stack([c("UGRD", f"{n} mb") for n in NIVELES_IVT])
    v = np.stack([c("VGRD", f"{n} mb") for n in NIVELES_IVT])
    iu, iv = ivt(q, u, v, NIVELES_IVT, c("PRES", "surface") / 100)
    mod = np.hypot(iu, iv)
    niveles = [250, 300, 400, 500, 600, 700, 800, 1000, 1200]
    m = ax.contourf(lon, lat, np.where(mod >= 250, mod, np.nan), levels=niveles, cmap="PuBuGn", extend="max",
                    norm=BoundaryNorm(niveles, 256), transform=ccrs.PlateCarree(), zorder=1, alpha=0.9)
    s = (slice(None, None, 4), slice(None, None, 4))
    fuerte = mod[s] >= 250
    ax.quiver(lon[s][fuerte], lat[s][fuerte], iu[s][fuerte], iv[s][fuerte], scale=18000, width=0.0018,
              color="#333333", transform=ccrs.PlateCarree(), zorder=5)
    _contornos(ax, lon, lat, pnm, np.arange(940, 1061, 4), "black", 0.8)
    _barra(fig, m, "IVT 1000-300 hPa (kg m⁻¹ s⁻¹) y su dirección · presión a nivel del mar (hPa)", niveles)
    salida["ivt"] = _png(fig)

    # 4) Vorticidad relativa, altura y viento en 500 hPa
    fig, ax = _marco(region, CARTAS["vorticidad_500"], ts, "rojo = ciclónica (negativa en el hemisferio sur)")
    zeta = suavizar(vorticidad_relativa(c("ABSV", "500 mb"), ref.lat) * 1e5, pasadas=3)
    niveles = [-24, -20, -16, -12, -8, -4, 4, 8, 12, 16, 20, 24]
    m = ax.contourf(lon, lat, np.where(np.abs(zeta) >= 4, zeta, np.nan), levels=niveles, cmap="RdBu",
                    extend="both", transform=ccrs.PlateCarree(), zorder=1, alpha=0.85)
    _contornos(ax, lon, lat, c("HGT", "500 mb") / 10, np.arange(480, 601, 6), "black", 1.0)
    _barbas(ax, lon, lat, c("UGRD", "500 mb"), c("VGRD", "500 mb"))
    _barra(fig, m, "Vorticidad relativa en 500 hPa (10⁻⁵ s⁻¹) · altura (dam) · viento (nudos)", niveles)
    salida["vorticidad_500"] = _png(fig)
    return salida
