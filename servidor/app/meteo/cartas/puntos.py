"""
Valores del ANÁLISIS del GFS en cada ciudad: para el meteograma de
diagnóstico ("lo que pasó" arriba de cada ciudad, cada 6 h), el sondeo del
modelo (perfil vertical, diagrama Skew-T) y el corte tiempo-altura.

Se sacan de los mismos archivos del GFS que se bajan para dibujar las cartas
de análisis (más los niveles que hacen falta para el perfil: temperatura,
humedad, altura y viento de 1000 a 100 hPa). Se guarda un JSON chico por hora
sinóptica, con todas las ciudades:

    ARCHIVO_DIR/cartas/_puntos/AAAA/MM/gfs_AAAAMMDDHH.json

Cada valor sale de interpolar (bilineal) los 4 puntos de la grilla de 0,5°
que rodean a la ciudad.
"""
import json
import math
from datetime import timezone
from pathlib import Path

import numpy as np

from ..ciudades import PUNTOS_GFS
from .diagnostico import ATM, G, KT, theta_e

CARPETA = "_puntos"
NIVELES_PERFIL = (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500,
                  450, 400, 350, 300, 250, 200, 150, 100)

# Lo que hace falta además de lo de las cartas (estilo_bentley.PEDIDOS).
PEDIDOS = ({(v, f"{n} mb") for v in ("TMP", "RH", "HGT", "UGRD", "VGRD") for n in NIVELES_PERFIL}
           | {("PRES", "surface"), ("HGT", "surface"), ("PRMSL", "mean sea level"),
              ("TMP", "2 m above ground"), ("DPT", "2 m above ground"), ("RH", "2 m above ground"),
              ("UGRD", "10 m above ground"), ("VGRD", "10 m above ground"),
              ("PWAT", ATM), ("CAPE", "surface"), ("CIN", "surface"), ("ABSV", "500 mb"), ("VVEL", "500 mb")}
           | {(v, f"{n} mb") for v in ("SPFH",) for n in (1000, 975, 950, 925, 900, 850, 800, 750, 700,
                                                         650, 600, 550, 500, 450, 400, 350, 300)})


def valor(campo, lon, lat):
    """Interpolación bilineal en la grilla regular del campo (lat de norte a sur)."""
    la, lo, v = campo.lat, campo.lon, campo.valores
    # Índices de la celda que contiene al punto (la lat va bajando).
    i = int(np.clip(np.searchsorted(-la, -lat) - 1, 0, len(la) - 2))
    j = int(np.clip(np.searchsorted(lo, lon) - 1, 0, len(lo) - 2))
    fy = (la[i] - lat) / (la[i] - la[i + 1])
    fx = (lon - lo[j]) / (lo[j + 1] - lo[j])
    return float((v[i, j] * (1 - fx) + v[i, j + 1] * fx) * (1 - fy)
                 + (v[i + 1, j] * (1 - fx) + v[i + 1, j + 1] * fx) * fy)


def rocio(t_c, hr):
    """Temperatura de rocío (°C) con la fórmula de Magnus."""
    hr = max(min(hr, 100.0), 1.0)
    g = math.log(hr / 100) + 17.67 * t_c / (t_c + 243.5)
    return 243.5 * g / (17.67 - g)


def viento(u, v):
    """(u, v) en m/s -> [intensidad en nudos, dirección de donde viene (°)]."""
    return [round(math.hypot(u, v) * KT, 1), round((math.degrees(math.atan2(-u, -v)) + 360) % 360)]


def extraer(campos, puntos=None):
    """{(variable, nivel): Campo} -> {ciudad: valores}. Los campos que falten
    se saltean (un análisis sin alguna variable no impide el resto)."""
    puntos = puntos or PUNTOS_GFS
    salida = {}
    for nombre, (lat, lon) in puntos.items():
        def x(var, nivel):
            c = campos.get((var, nivel))
            return None if c is None else valor(c, lon, lat)

        r = {}
        psup = x("PRES", "surface")
        if psup is not None:
            psup /= 100
        pnm = x("PRMSL", "mean sea level")
        if pnm is not None:
            r["pnm"] = round(pnm / 100, 1)
        z1000, z500 = x("HGT", "1000 mb"), x("HGT", "500 mb")
        if z1000 is not None and z500 is not None:
            r["espesor"] = round((z500 - z1000) / 10, 1)                     # dam
        for n in (850, 700, 500):
            z = x("HGT", f"{n} mb")
            if z is not None:
                r[f"z{n}"] = round(z / 10, 1)                                 # dam
        for n in (850, 500):
            t = x("TMP", f"{n} mb")
            if t is not None:
                r[f"t{n}"] = round(t - 273.15, 1)
        t850, hr850 = x("TMP", "850 mb"), x("RH", "850 mb")
        if t850 is not None and hr850 is not None:
            r["td850"] = round(rocio(t850 - 273.15, hr850), 1)
            r["te850"] = round(float(theta_e(np.array(t850), np.array(hr850), 850.0)), 1)
        for n in (850, 500, 250):
            u, v = x("UGRD", f"{n} mb"), x("VGRD", f"{n} mb")
            if u is not None and v is not None:
                r[f"v{n}"] = viento(u, v)
        abs500 = x("ABSV", "500 mb")
        if abs500 is not None:
            f = 2 * 7.2921e-5 * math.sin(math.radians(lat))
            r["vort500"] = round((abs500 - f) * 1e5, 1)                       # 10⁻⁵ s⁻¹ (ciclónica < 0)
        w = x("VVEL", "500 mb")
        if w is not None:
            r["omega500"] = round(w, 2)                                       # Pa/s (ascenso < 0)
        for k, var, nivel, esc in (("pw", "PWAT", ATM, 1), ("cape", "CAPE", "surface", 1), ("cin", "CIN", "surface", 1)):
            val = x(var, nivel)
            if val is not None:
                r[k] = round(val * esc, 1)
        # IVT (1000-300 hPa) en el punto: (1/g) ∫ q·V dp, sin los niveles bajo el suelo.
        qs = [(n, x("SPFH", f"{n} mb"), x("UGRD", f"{n} mb"), x("VGRD", f"{n} mb"))
              for n in (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300)]
        qs = [(n, q, u, v) for n, q, u, v in qs if None not in (q, u, v) and (psup is None or n <= psup)]
        if len(qs) >= 2:
            p = np.array([n for n, *_ in qs]) * 100.0
            qu = np.array([q * u for _, q, u, _ in qs])
            qv = np.array([q * v for _, q, _, v in qs])
            iu, iv = -np.trapezoid(qu, p) / G, -np.trapezoid(qv, p) / G      # p baja con la altura
            r["ivt"] = [round(float(math.hypot(iu, iv))), round((math.degrees(math.atan2(iu, iv)) + 360) % 360)]
        # Superficie del modelo (2 m y 10 m).
        sup = {}
        for k, var, nivel in (("t", "TMP", "2 m above ground"), ("td", "DPT", "2 m above ground")):
            val = x(var, nivel)
            if val is not None:
                sup[k] = round(val - 273.15, 1)
        u10, v10 = x("UGRD", "10 m above ground"), x("VGRD", "10 m above ground")
        if u10 is not None and v10 is not None:
            sup["v"] = viento(u10, v10)
        zsup = x("HGT", "surface")
        if psup is not None:
            sup["p"] = round(psup, 1)
        if zsup is not None:
            sup["z"] = round(zsup)
        r["sup"] = sup
        # Perfil vertical (para el sondeo): solo los niveles sobre el suelo.
        perfil = {"p": [], "z": [], "t": [], "td": [], "dir": [], "kt": []}
        for n in NIVELES_PERFIL:
            if psup is not None and n > psup:
                continue
            t, hr, z = x("TMP", f"{n} mb"), x("RH", f"{n} mb"), x("HGT", f"{n} mb")
            u, v = x("UGRD", f"{n} mb"), x("VGRD", f"{n} mb")
            if None in (t, hr, z, u, v):
                continue
            kt, d = viento(u, v)
            perfil["p"].append(n)
            perfil["z"].append(round(z))
            perfil["t"].append(round(t - 273.15, 1))
            perfil["td"].append(round(rocio(t - 273.15, hr), 1))
            perfil["dir"].append(d)
            perfil["kt"].append(kt)
        if perfil["p"]:
            r["perfil"] = perfil
        salida[nombre] = r
    return salida


def ruta(raiz, ts):
    ts = ts.astimezone(timezone.utc)
    return Path(raiz) / "cartas" / CARPETA / f"{ts:%Y}" / f"{ts:%m}" / f"gfs_{ts:%Y%m%d%H}.json"


def existe(raiz, ts):
    return ruta(raiz, ts).exists()


def guardar(raiz, ts, valores):
    destino = ruta(raiz, ts)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": ts.astimezone(timezone.utc).isoformat(), "ciudades": valores},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(destino)


def leer(raiz, ts):
    try:
        return json.loads(ruta(raiz, ts).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
