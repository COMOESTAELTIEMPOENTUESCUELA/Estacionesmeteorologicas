"""
Radiosondeos: el REAL (el globo que lanza el SMN en Ezeiza) y los índices
de cualquier perfil (el real o el del análisis del GFS, puntos.py).

El real sale de IGRA (Integrated Global Radiosonde Archive, NOAA/NCEI), el
archivo oficial de radiosondeos del mundo. Para cada estación hay un archivo
"del año hasta hoy" que se actualiza todos los días (con 1 o 2 días de
atraso):

    https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive/access/data-y2d/
        ARM00087576-data-beg2025.txt.zip      (Ezeiza; ARM = Argentina)

Formato (igra2-data-format.txt), un encabezado por sondeo y una línea por nivel:

    #ARM00087576 2026 10 29 12 1115   48 ncdc-gts ncdc-gts -348200 -585300
    21 -9999  101800B   20B  158B  712   46   20   36
    ...

Columnas de cada nivel (posiciones fijas): tipo de nivel (1-2), tiempo
transcurrido (4-8), presión en Pa (10-15), altura en m (17-21), temperatura en
décimas de °C (23-27), humedad relativa en décimas de % (29-33), depresión del
punto de rocío en décimas de °C (35-39), dirección del viento (41-45),
velocidad en décimas de m/s (47-51). -9999 = falta, -8888 = descartado.
"""
import io
import json
import math
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

CARPETA = "_sondeos"
IGRA_Y2D = "https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive/access/data-y2d/"
KT = 1.943844


def _entero(texto):
    try:
        v = int(texto)
    except ValueError:
        return None
    return None if v in (-9999, -8888) else v


def leer_igra(texto):
    """Texto de un archivo IGRA2 -> [{"ts", "niveles": [{p, z, t, td, dir, kt}]}].
    Los niveles sin presión o sin temperatura se saltean (el viento solo, sin
    temperatura, no sirve para el Skew-T)."""
    sondeos, actual = [], None
    for linea in texto.splitlines():
        if linea.startswith("#"):
            hora = int(linea[24:26])
            if hora == 99:                                     # hora desconocida: se usa la de lanzamiento
                rel = int(linea[27:31])
                hora = rel // 100 if rel != 9999 else 0
            ts = datetime(int(linea[13:17]), int(linea[18:20]), int(linea[21:23]), hora, tzinfo=timezone.utc)
            actual = {"ts": ts, "niveles": []}
            sondeos.append(actual)
            continue
        if actual is None or len(linea) < 51:
            continue
        p, z, t = _entero(linea[9:15]), _entero(linea[16:21]), _entero(linea[22:27])
        dpd, wd, ws = _entero(linea[34:39]), _entero(linea[40:45]), _entero(linea[46:51])
        if p is None or t is None:
            continue
        nivel = {"p": round(p / 100, 1), "z": z, "t": t / 10, "td": None if dpd is None else round((t - dpd) / 10, 1),
                 "dir": wd, "kt": None if ws is None else round(ws / 10 * KT, 1)}
        actual["niveles"].append(nivel)
    for s in sondeos:
        # De abajo hacia arriba, sin presiones repetidas (IGRA mezcla niveles tipo y significativos).
        vistos, niveles = set(), []
        for n in sorted(s["niveles"], key=lambda n: -n["p"]):
            if n["p"] not in vistos:
                vistos.add(n["p"])
                niveles.append(n)
        s["niveles"] = niveles
    return [s for s in sondeos if len(s["niveles"]) >= 5]


def nombre_archivo_igra(listado, estacion):
    """El nombre del archivo y2d de la estación en el listado de la carpeta
    (el año de "beg" cambia cada año)."""
    m = re.findall(rf"{estacion}-data-beg\d{{4}}\.txt\.zip", listado)
    return sorted(m)[-1] if m else None


def bajar_igra(estacion, sesion):
    """Todos los sondeos del archivo y2d de la estación (IGRA)."""
    r = sesion.get(IGRA_Y2D, timeout=60)
    r.raise_for_status()
    nombre = nombre_archivo_igra(r.text, estacion)
    if not nombre:
        raise FileNotFoundError(f"IGRA no tiene archivo del año para {estacion}")
    r = sesion.get(IGRA_Y2D + nombre, timeout=180)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        texto = z.read(z.namelist()[0]).decode("ascii", "replace")
    return leer_igra(texto)


def ruta(raiz, estacion, ts):
    ts = ts.astimezone(timezone.utc)
    return Path(raiz) / "cartas" / CARPETA / estacion / f"{ts:%Y}" / f"{ts:%m}" / f"{estacion}_{ts:%Y%m%d%H}.json"


def guardar(raiz, estacion, sondeo):
    destino = ruta(raiz, estacion, sondeo["ts"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"ts": sondeo["ts"].isoformat(), "niveles": sondeo["niveles"]},
                                  separators=(",", ":")), encoding="utf-8")


def leer(raiz, estacion, ts):
    try:
        return json.loads(ruta(raiz, estacion, ts).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Índices del sondeo (de cualquier perfil: el real o el del GFS)
# ---------------------------------------------------------------------------
RD, CP, LV, EPS = 287.04, 1005.7, 2.501e6, 0.622


def presion_vapor(td_c):
    """Presión de vapor de saturación (hPa) a td_c (°C), Bolton (1980)."""
    return 6.112 * math.exp(17.67 * td_c / (td_c + 243.5))


def razon_mezcla(td_c, p):
    e = presion_vapor(td_c)
    return EPS * e / (p - e)                               # kg/kg


def ncl(t_c, td_c, p):
    """Nivel de condensación por ascenso (NCL, en inglés LCL): (p, T en °C).
    Temperatura de Bolton (1980) y presión por la adiabática seca."""
    tk, tdk = t_c + 273.15, td_c + 273.15
    tl = 1 / (1 / (tdk - 56) + math.log(tk / tdk) / 800) + 56
    return p * (tl / tk) ** (CP / RD), tl - 273.15


def _dtdp_humeda(tk, p):
    """Variación de la temperatura con la presión (K/hPa) de una parcela
    saturada que asciende (adiabática húmeda)."""
    rs = razon_mezcla(tk - 273.15, p)
    return (RD * tk + LV * rs) / (p * (CP + LV ** 2 * rs * EPS / (RD * tk ** 2)))


def trayectoria_parcela(t_c, td_c, p0, ps):
    """Temperatura (°C) de una parcela que sube desde p0 (con T y Td), en
    cada presión de ps (de mayor a menor): adiabática seca hasta el NCL y
    húmeda desde ahí."""
    p_ncl, t_ncl = ncl(t_c, td_c, p0)
    salida, tk, p = [], t_ncl + 273.15, p_ncl
    for pp in ps:
        if pp >= p_ncl:
            salida.append((t_c + 273.15) * (pp / p0) ** (RD / CP) - 273.15)
            continue
        while p - 5 > pp:                                   # se integra de a 5 hPa
            tk -= _dtdp_humeda(tk, p) * 5
            p -= 5
        tk -= _dtdp_humeda(tk, p) * (p - pp)
        p = pp
        salida.append(tk - 273.15)
    return salida


def _interp_log(p, ps, vs):
    """Valor en p interpolando en log(p) (ps de mayor a menor)."""
    for i in range(1, len(ps)):
        if ps[i] <= p <= ps[i - 1]:
            f = math.log(ps[i - 1] / p) / math.log(ps[i - 1] / ps[i])
            return vs[i - 1] + f * (vs[i] - vs[i - 1])
    return None


def indices(niveles):
    """Índices de un perfil [{p, z, t, td, ...}] ordenado de abajo hacia arriba:
    agua precipitable (mm), altura de la isoterma de 0 °C (m sobre el suelo),
    NCL (hPa), CAPE y CIN de la parcela de superficie (J/kg), índice K,
    índice de levantamiento LI, y la trayectoria de la parcela (para dibujarla)."""
    ok = [n for n in niveles if n.get("t") is not None and n.get("td") is not None and n.get("p")]
    if len(ok) < 5:
        return {}
    ps = [n["p"] for n in ok]
    t = [n["t"] for n in ok]
    td = [n["td"] for n in ok]
    r = {}
    # Agua precipitable: (1/g) ∫ q dp.
    q = [razon_mezcla(x, p) for x, p in zip(td, ps)]
    r["pw"] = round(sum((q[i] + q[i - 1]) / 2 * (ps[i - 1] - ps[i]) * 100 for i in range(1, len(ps))) / 9.80665, 1)
    # Isoterma de 0 °C: la primera vez que la temperatura pasa a negativa (desde abajo).
    zs = [n.get("z") for n in ok]
    for i in range(1, len(ok)):
        if t[i - 1] > 0 >= t[i] and zs[i] is not None and zs[i - 1] is not None and zs[0] is not None:
            z0 = zs[i - 1] + (zs[i] - zs[i - 1]) * t[i - 1] / (t[i - 1] - t[i])
            r["z0"] = round(z0 - zs[0])
            break
    p_ncl, _ = ncl(t[0], td[0], ps[0])
    r["ncl"] = round(p_ncl)
    # Parcela de superficie: CAPE (flotación positiva arriba del nivel de
    # convección libre) y CIN (lo negativo debajo), con Rd ∫ (Tp - Te) d ln p.
    tp = trayectoria_parcela(t[0], td[0], ps[0], ps)
    r["parcela"] = [round(x, 1) for x in tp]
    cape = cin = 0.0
    libre = False
    for i in range(1, len(ps)):
        a = ((tp[i] - t[i]) + (tp[i - 1] - t[i - 1])) / 2
        e = RD * a * math.log(ps[i - 1] / ps[i])
        if a > 0:
            libre = True
            cape += e
        elif not libre:
            cin += e
    r["cape"], r["cin"] = round(cape), round(cin) if cape > 0 else 0
    v = {k: _interp_log(k, ps, t) for k in (850, 700, 500)}
    vd = {k: _interp_log(k, ps, td) for k in (850, 700)}
    if None not in v.values() and None not in vd.values():
        r["k"] = round((v[850] - v[500]) + vd[850] - (v[700] - vd[700]), 1)
    tp500 = _interp_log(500, ps, tp)
    if tp500 is not None and v[500] is not None:
        r["li"] = round(v[500] - tp500, 1)
    return r
