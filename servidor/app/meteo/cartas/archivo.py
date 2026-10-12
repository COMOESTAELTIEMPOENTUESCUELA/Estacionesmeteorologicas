"""
Archivo de cartas en disco:

    {ARCHIVO}/cartas/<producto>/<AAAA>/<MM>/<producto>_<AAAAMMDDHH>.png

    {ARCHIVO}/cartas/_fondos/<nombre>.png   (geografía de los mapas de Ogimet)

Una carta por producto y hora (UTC). No hace falta base de datos: el nombre
del archivo ya dice qué es, y el visualizador lista las carpetas.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

FIRMAS = {b"\x89PNG\r\n\x1a\n": "png", b"GIF87a": "gif", b"GIF89a": "gif", b"\xff\xd8\xff": "jpg"}


def tipo_imagen(datos):
    """Devuelve la extensión si los bytes son una imagen de verdad (y no,
    por ejemplo, una página de error con código 200)."""
    for firma, ext in FIRMAS.items():
        if datos[:len(firma)] == firma:
            return ext
    return None


def ruta(raiz, producto, ts, ext="png"):
    ts = ts.astimezone(timezone.utc)
    return Path(raiz) / "cartas" / producto / f"{ts:%Y}" / f"{ts:%m}" / f"{producto}_{ts:%Y%m%d%H}.{ext}"


def existe(raiz, producto, ts):
    base = ruta(raiz, producto, ts)
    return any(base.with_suffix(f".{e}").exists() for e in ("png", "gif", "jpg"))


def guardar(raiz, producto, ts, datos):
    ext = tipo_imagen(datos)
    if not ext:
        raise ValueError("lo descargado no es una imagen")
    destino = ruta(raiz, producto, ts, ext)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    tmp.write_bytes(datos)
    os.replace(tmp, destino)   # atómico: nunca queda una imagen a medio escribir
    return destino


def disponibles(raiz, productos, anio_mes=None):
    """{ts_iso: [productos]} de lo archivado (todo, o solo un mes 'AAAA-MM')."""
    salida = {}
    for p in productos:
        base = Path(raiz) / "cartas" / p
        patron = f"{anio_mes[:4]}/{anio_mes[5:7]}/*" if anio_mes else "*/*/*"
        for f in base.glob(patron):
            if f.suffix == ".tmp":
                continue
            try:
                ts = datetime.strptime(f.stem.rsplit("_", 1)[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
            except (IndexError, ValueError):
                continue
            salida.setdefault(ts.isoformat().replace("+00:00", "Z"), []).append(p)
    return dict(sorted(salida.items()))


def ruta_fondo(raiz, nombre, ext="png"):
    return Path(raiz) / "cartas" / "_fondos" / f"{nombre}.{ext}"


def fondo(raiz, nombre):
    """El archivo de fondo guardado (o None)."""
    for e in ("png", "gif", "jpg"):
        f = ruta_fondo(raiz, nombre, e)
        if f.exists():
            return f
    return None


def guardar_fondo(raiz, nombre, datos):
    ext = tipo_imagen(datos)
    if not ext:
        raise ValueError("lo descargado no es una imagen")
    destino = ruta_fondo(raiz, nombre, ext)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    tmp.write_bytes(datos)
    os.replace(tmp, destino)
    return destino
