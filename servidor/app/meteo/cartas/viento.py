"""
El viento de cada plazo del pronóstico GFS, para animarlo encima de las cartas
(partículas que siguen el flujo, como Windy). Lo dibuja el navegador; acá solo
se guardan los números.

El GFS ya se baja para dibujar las cartas (estilo_bentley.PEDIDOS trae u y v
en 250, 500, 700 y 850 hPa): al dibujar cada plazo se guarda además una grilla
chica del viento, cada 1,5° (PASO puntos de la de 0,5°), en m/s redondeados a
un entero con signo (int8: -127 a 127, de sobra para el viento). Por plazo:
4 niveles × 2 componentes × ~9000 puntos = ~72 KB. Se guarda en
    ARCHIVO_DIR/cartas/_pronostico/_viento/<corrida AAAAMMDDHH>/
        grilla.json   la grilla (lat0, dlat, nlat, lon0, dlon, nlon) y el orden de los niveles
        f048.bin      los bytes: [nivel][u, v][lat][lon]
y se borra con la corrida (limpiar), salvo las fijadas.
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

CARPETA = "_pronostico/_viento"
NIVELES = ["250", "500", "700", "850"]           # hPa; el orden de los bytes
PASO = 3                                        # cada 3 puntos de 0,5° = 1,5°


def carpeta(raiz, inicio):
    return Path(raiz) / "cartas" / CARPETA / f"{inicio.astimezone(timezone.utc):%Y%m%d%H}"


def guardar(raiz, inicio, plazo, campos):
    """campos: los del GFS para ese plazo ({(var, nivel): Campo}). Si falta
    algún nivel, no guarda nada (devuelve False)."""
    try:
        comps = [campos[(c, f"{n} mb")] for n in NIVELES for c in ("UGRD", "VGRD")]
    except KeyError:
        return False
    ref = comps[0]
    lat, lon = np.asarray(ref.lat)[::PASO], np.asarray(ref.lon)[::PASO]
    datos = np.stack([np.clip(np.rint(np.asarray(c.valores)[::PASO, ::PASO]), -127, 127) for c in comps]).astype(np.int8)
    d = carpeta(raiz, inicio)
    d.mkdir(parents=True, exist_ok=True)
    grilla = d / "grilla.json"
    if not grilla.exists():
        tmp = grilla.with_suffix(".tmp")
        tmp.write_text(json.dumps({"lat0": float(lat[0]), "dlat": float(lat[1] - lat[0]), "nlat": len(lat),
                                   "lon0": float(lon[0]), "dlon": float(lon[1] - lon[0]), "nlon": len(lon),
                                   "niveles": NIVELES, "unidad": "m/s"}), encoding="utf-8")
        tmp.replace(grilla)
    archivo = d / f"f{plazo:03d}.bin"
    tmp = archivo.with_suffix(".tmp")
    tmp.write_bytes(datos.tobytes())
    tmp.replace(archivo)                         # la web nunca sirve uno a medias
    return True


def limpiar(raiz, limite, fijas=()):
    """Borra el viento de las corridas que empezaron antes de `limite`, salvo las de `fijas`."""
    base = Path(raiz) / "cartas" / CARPETA
    if not base.exists():
        return 0
    n = 0
    for d in base.iterdir():
        try:
            t = datetime.strptime(d.name, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t < limite and t not in fijas:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n
