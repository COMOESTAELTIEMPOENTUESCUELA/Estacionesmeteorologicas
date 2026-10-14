"""
Archivo de cartas en disco:

    {ARCHIVO}/cartas/<producto>/<AAAA>/<MM>/<producto>_<AAAAMMDDHH>.png

    {ARCHIVO}/cartas/_fondos/<nombre>.png   (geografía de los mapas de Ogimet)
    {ARCHIVO}/cartas/_min/<producto>/<AAAA>/<MM>/<producto>_<AAAAMMDDHH>.webp
                                             (miniatura liviana para la vista "placa")
    {ARCHIVO}/cartas/_areas/<producto>/<AAAA>/<MM>/<producto>_<AAAAMMDDHH>.json
                                             (Ogimet: qué estación hay en cada lugar del mapa)

Una carta por producto y hora (UTC). No hace falta base de datos: el nombre
del archivo ya dice qué es, y el visualizador lista las carpetas.
"""
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
# Miniaturas: solo para las cartas pesadas (Bentley, ~200 KB). Las de Ogimet
# pesan ~30 KB y son transparentes (llevan el fondo aparte): van tal cual.
MINIATURA_DESDE_BYTES = 100_000
MINIATURA_ANCHO = 640

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


def _escribir(destino, datos):
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    tmp.write_bytes(datos)
    os.replace(tmp, destino)   # atómico: nunca queda una imagen a medio escribir


def guardar(raiz, producto, ts, datos):
    ext = tipo_imagen(datos)
    if not ext:
        raise ValueError("lo descargado no es una imagen")
    destino = ruta(raiz, producto, ts, ext)
    _escribir(destino, datos)
    try:
        guardar_miniatura(raiz, producto, ts, datos)
    except Exception as ex:          # sin miniatura la página usa la imagen completa
        log.warning("miniatura de %s %s: %s", producto, ts, ex)
    return destino


def ruta_miniatura(raiz, producto, ts):
    ts = ts.astimezone(timezone.utc)
    return Path(raiz) / "cartas" / "_min" / producto / f"{ts:%Y}" / f"{ts:%m}" / f"{producto}_{ts:%Y%m%d%H}.webp"


def guardar_miniatura(raiz, producto, ts, datos):
    """Versión chica (640 px de ancho, WebP) para la vista "placa", donde
    cada carta se ve chica: pesa 5 a 10 veces menos. La completa se baja
    solo al abrirla en grande. Devuelve la ruta, o None si no hace falta."""
    if len(datos) < MINIATURA_DESDE_BYTES:
        return None
    from PIL import Image
    with Image.open(io.BytesIO(datos)) as im:
        im = im.convert("RGB")
        im.thumbnail((MINIATURA_ANCHO, MINIATURA_ANCHO * 4))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=80, method=6)
    destino = ruta_miniatura(raiz, producto, ts)
    _escribir(destino, buf.getvalue())
    return destino


def completar_miniaturas(raiz, productos):
    """Crea las miniaturas que falten (para lo archivado antes de que existieran)."""
    nuevas = 0
    for p in productos:
        for f in sorted((Path(raiz) / "cartas" / p).glob("*/*/*")):
            if f.suffix not in (".png", ".gif", ".jpg"):
                continue
            try:
                ts = datetime.strptime(f.stem.rsplit("_", 1)[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
            except (IndexError, ValueError):
                continue
            if ruta_miniatura(raiz, p, ts).exists():
                continue
            try:
                if guardar_miniatura(raiz, p, ts, f.read_bytes()):
                    nuevas += 1
            except Exception as ex:
                log.warning("miniatura de %s: %s", f.name, ex)
    return nuevas


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


def ruta_areas(raiz, producto, ts):
    """Zonas clickeables de un mapa de Ogimet (qué estación hay en cada lugar)."""
    ts = ts.astimezone(timezone.utc)
    return Path(raiz) / "cartas" / "_areas" / producto / f"{ts:%Y}" / f"{ts:%m}" / f"{producto}_{ts:%Y%m%d%H}.json"


def guardar_areas(raiz, producto, ts, lista):
    import json
    _escribir(ruta_areas(raiz, producto, ts), json.dumps(lista, ensure_ascii=False).encode("utf-8"))


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
    _escribir(destino, datos)
    return destino
