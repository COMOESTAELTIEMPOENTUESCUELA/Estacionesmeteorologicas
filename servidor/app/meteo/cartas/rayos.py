"""
Mapa propio de rayos: los destellos que vio el GLM (el detector de relámpagos
del GOES-19) en los 60 minutos previos a la hora de la carta, sobre el
infrarrojo (canal 13) de esa hora, recortado a la región que nos interesa.

De dónde salen los datos (gratis, en el mismo bucket público de NOAA que usa
el módulo de satélite):

    GLM-L2-LCFA / 2026 / 295 / 12 / OR_GLM-L2-LCFA_G19_s20262951200000_e..._c....nc

- LCFA = "Lightning Cluster Filter Algorithm": el GLM detecta "eventos" (un
  píxel que se ilumina), los agrupa en "grupos" y los grupos en "destellos"
  (flashes). Un destello es, aproximadamente, un rayo. Usamos los destellos.
- Un archivo cada 20 segundos (180 por hora), de unos 100-500 KB: la lista de
  destellos de esos 20 s en todo lo que ve el satélite.
- Se guardan para siempre en el bucket: un evento de hace meses se puede
  dibujar igual (`historico cartas --desde ... --hasta ... --forzar`).

Para no bajar 180 archivos cada hora "por las dudas", el mapa se arma solo
si alguna estación del SMN de la región informó tormenta en el SYNOP (tiempo
presente) cerca de esa hora: ver `WW_TORMENTA` y tareas.hubo_tormenta().
"""
import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import h5netcdf
import numpy as np

from ..satelite import procesar, render, s3

log = logging.getLogger(__name__)

BUCKET = "noaa-goes19"
PRODUCTO_GLM = "GLM-L2-LCFA"
_INICIO = re.compile(r"OR_GLM-L2-LCFA_G\d{2}_s(\d{14})")

# Tiempo presente (tabla 4677 de la OMM) que indica tormenta:
# 13 relámpagos sin trueno · 17 tormenta sin precipitación · 29 tormenta en la
# hora anterior · 91-99 precipitación con tormenta (ahora o en la hora anterior).
WW_TORMENTA = [13, 17, 29] + list(range(91, 100))


# ---------------------------------------------------------------------------
# Archivos del GLM en el bucket
# ---------------------------------------------------------------------------
def listar_glm(desde, hasta, sesion, bucket=BUCKET):
    """[(inicio, url)] de los archivos del GLM que empiezan en [desde, hasta)."""
    salida = []
    hora = desde.replace(minute=0, second=0, microsecond=0)
    while hora < hasta:
        prefijo = f"{PRODUCTO_GLM}/{hora:%Y}/{hora:%j}/{hora:%H}/"
        token = None
        while True:
            params = {"list-type": "2", "prefix": prefijo}
            if token:
                params["continuation-token"] = token
            r = sesion.get(f"https://{bucket}.s3.amazonaws.com/", params=params, timeout=30)
            r.raise_for_status()
            raiz = ET.fromstring(r.content)
            for obj in raiz.findall("s3:Contents", s3.NS):
                clave = obj.findtext("s3:Key", namespaces=s3.NS)
                m = _INICIO.search(clave or "")
                if m:
                    t = s3.parsear_inicio(m.group(1))
                    if desde <= t < hasta:
                        salida.append((t, f"https://{bucket}.s3.amazonaws.com/{clave}"))
            if raiz.findtext("s3:IsTruncated", namespaces=s3.NS) == "true":
                token = raiz.findtext("s3:NextContinuationToken", namespaces=s3.NS)
            else:
                break
        hora += timedelta(hours=1)
    return sorted(salida)


# ---------------------------------------------------------------------------
# Leer los destellos de un archivo
# ---------------------------------------------------------------------------
def _valores(var):
    """Variable de h5netcdf -> números reales. h5netcdf entrega los enteros
    tal cual están guardados: hay que aplicar a mano _Unsigned (el GLM guarda
    tiempos y energías como enteros SIN signo), scale_factor y add_offset."""
    crudo = np.asarray(var[:])
    if str(var.attrs.get("_Unsigned", b"")).strip("b'\"").lower() == "true" and crudo.dtype.kind == "i":
        crudo = crudo.view(crudo.dtype.str.replace("i", "u"))
    a = crudo.astype("float64")
    relleno = var.attrs.get("_FillValue")
    a = a * float(var.attrs.get("scale_factor", 1.0)) + float(var.attrs.get("add_offset", 0.0))
    if relleno is not None:
        a[crudo == np.asarray(relleno).astype(crudo.dtype)] = np.nan
    return a


def _origen_de_tiempos(unidades, respaldo):
    """'seconds since 2026-10-22 12:00:00.000' -> datetime UTC."""
    texto = unidades.decode() if isinstance(unidades, bytes) else str(unidades or "")
    m = re.search(r"since (\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", texto)
    if not m:
        return respaldo
    return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=timezone.utc)


def leer_destellos(datos, inicio_archivo, region):
    """Bytes de un archivo LCFA -> [(t, lat, lon, energía en J)] dentro de la región."""
    lon0, lon1, lat0, lat1 = region
    with h5netcdf.File(io.BytesIO(datos), "r") as nc:
        if "flash_lat" not in nc.variables or nc.dimensions["number_of_flashes"].size == 0:
            return []
        lat = _valores(nc.variables["flash_lat"])
        lon = _valores(nc.variables["flash_lon"])
        vt = nc.variables["flash_time_offset_of_first_event"]
        seg = _valores(vt)
        origen = _origen_de_tiempos(vt.attrs.get("units"), inicio_archivo)
        energia = _valores(nc.variables["flash_energy"]) if "flash_energy" in nc.variables else np.full(lat.shape, np.nan)
    dentro = (lon >= lon0) & (lon <= lon1) & (lat >= lat0) & (lat <= lat1) & np.isfinite(seg)
    return [(origen + timedelta(seconds=float(s)), float(la), float(lo), float(e))
            for s, la, lo, e in zip(seg[dentro], lat[dentro], lon[dentro], energia[dentro])]


def destellos(desde, hasta, region, sesion):
    """Todos los destellos de la región entre desde y hasta (baja ~180 archivos por hora)."""
    lista, bytes_bajados = [], 0
    for inicio, url in listar_glm(desde, hasta, sesion):
        r = sesion.get(url, timeout=60)
        r.raise_for_status()
        bytes_bajados += len(r.content)
        lista += [d for d in leer_destellos(r.content, inicio, region) if desde <= d[0] < hasta]
    log.info("rayos: %d destellos en la región (%.0f MB bajados)", len(lista), bytes_bajados / 1e6)
    return sorted(lista)


# ---------------------------------------------------------------------------
# El mapa
# ---------------------------------------------------------------------------
def infrarrojo(ts, region, sesion):
    """Recorte del canal 13 del escaneo de disco completo de la hora ts."""
    archivos = s3.listar_rango(BUCKET, "ABI-L2-CMIPF", 13, ts - timedelta(minutes=10), ts + timedelta(minutes=2), sesion)
    if not archivos:
        raise FileNotFoundError(f"no hay canal 13 para {ts:%d/%m %H:%M} UTC")
    a = min(archivos, key=lambda x: abs(x.inicio - ts))
    return procesar.recortar(a.url(BUCKET), region, sesion=sesion, con_dqf=False)


def dibujar(rec, lista, ts, minutos, region, titulo_region, puntos, tormentas):
    """PNG (bytes) del infrarrojo en grises con los destellos coloreados por
    antigüedad: amarillo = recién, violeta = hace una hora."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    import cartopy.crs as ccrs

    def rayos(fig, ax):
        if lista:
            hace = np.array([(ts - t).total_seconds() / 60 for t, *_ in lista])
            lat = np.array([la for _, la, _, _ in lista])
            lon = np.array([lo for _, _, lo, _ in lista])
            orden = np.argsort(-hace)       # los más recientes, arriba
            sc = ax.scatter(lon[orden], lat[orden], c=hace[orden], cmap="plasma_r", norm=Normalize(0, minutos),
                            s=9, marker="+", linewidths=0.9, transform=ccrs.PlateCarree(), zorder=5)
            # Escala de los rayos: recuadro dentro del mapa, abajo a la derecha (sobre el mar).
            cax = ax.inset_axes([0.64, 0.06, 0.32, 0.022])
            cb = fig.colorbar(sc, cax=cax, orientation="horizontal")
            cb.set_label("rayos: minutos antes de la hora", fontsize=6.5, color="white")
            cb.ax.tick_params(labelsize=6, colors="white")
            cb.outline.set_edgecolor("white")
        texto = (f"{len(lista)} destellos (GLM) en los {minutos} min previos"
                 + (f" · Tormenta en SYNOP: {tormentas}" if tormentas else ""))
        ax.text(0.01, 0.99, texto, transform=ax.transAxes, va="top", fontsize=7.5, zorder=6,
                color="#ffd400" if lista else "white", bbox={"facecolor": "black", "alpha": 0.6, "pad": 3, "linewidth": 0})

    buf = io.BytesIO()
    render.dibujar(rec, buf, region, "ir_gris", titulo_region, puntos, extra=rayos,
                   titulo=f"GOES-19 · Rayos (GLM) sobre infrarrojo (canal 13)",
                   pie="Datos: NOAA/NESDIS (GOES-19 GLM L2 LCFA y ABI L2 CMIP) · Procesado localmente")
    plt.close("all")
    return buf.getvalue()


def ruta_destellos(raiz, producto, ts):
    ts = ts.astimezone(timezone.utc)
    return Path(raiz) / "cartas" / "_rayos" / producto / f"{ts:%Y}" / f"{ts:%m}" / f"{producto}_{ts:%Y%m%d%H}.csv"


def guardar_destellos(raiz, producto, ts, lista):
    """La lista de destellos de la región (para contarlos o analizarlos después)."""
    destino = ruta_destellos(raiz, producto, ts)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_utc", "lat", "lon", "energia_J"])
        for t, la, lo, e in lista:
            w.writerow([t.isoformat().replace("+00:00", "Z"), f"{la:.4f}", f"{lo:.4f}", "" if np.isnan(e) else f"{e:.3e}"])
