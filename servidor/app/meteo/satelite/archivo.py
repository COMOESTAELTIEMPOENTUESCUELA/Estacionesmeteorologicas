"""
Dónde y cómo se guardan los archivos en disco, y los índices que lee el visor.

    {ARCHIVO}/satelite/
        catalogo.json                       <- lista de productos (lo lee el visor)
        ir13_centro/
            ultimas.json                    <- últimas N imágenes (para la animación)
            2026/10/10/
                index.json                  <- todas las imágenes de ese día
                ir13_centro_20261010_1200.png
                ir13_centro_20261010_1200.nc

Organizar por año/mes/día hace que cada carpeta tenga pocos archivos (rápido
de listar) y que buscar "todo lo del 14 de octubre" sea abrir una carpeta.
Los índices son JSON simples: el visor web no necesita base de datos para
mostrar las imágenes. Más adelante, la base de datos los va a indexar también.
"""
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class Archivo:
    def __init__(self, raiz):
        self.raiz = Path(raiz) / "satelite"
        self.raiz.mkdir(parents=True, exist_ok=True)

    def carpeta_dia(self, producto, t):
        return self.raiz / producto / f"{t:%Y}" / f"{t:%m}" / f"{t:%d}"

    def nombre_base(self, producto, t):
        return f"{producto}_{t:%Y%m%d_%H%M}"

    def rutas(self, producto, t, ext_imagen="png"):
        d = self.carpeta_dia(producto, t)
        base = self.nombre_base(producto, t)
        return d / f"{base}.{ext_imagen}", d / f"{base}.nc"

    def ya_procesado(self, producto, t, ext_imagen="png"):
        img, _ = self.rutas(producto, t, ext_imagen)
        return img.exists()

    # ---- índices ----
    def registrar(self, producto, t, archivos, extra=None, max_ultimas=144):
        """Agrega una entrada al índice del día y a ultimas.json.
        `archivos` = {"imagen": Path, "datos": Path|None}"""
        dia = self.carpeta_dia(producto, t)
        entrada = {
            "t": t.strftime("%Y-%m-%dT%H:%MZ"),
            **{k: str(v.relative_to(self.raiz)) for k, v in archivos.items() if v is not None},
            **(extra or {}),
        }
        self._agregar(dia / "index.json", entrada, limite=None)
        self._agregar(self.raiz / producto / "ultimas.json", entrada, limite=max_ultimas)

    def _agregar(self, ruta, entrada, limite):
        lista = _leer_json(ruta, [])
        lista = [e for e in lista if e["t"] != entrada["t"]] + [entrada]
        lista.sort(key=lambda e: e["t"])
        if limite:
            lista = lista[-limite:]
        _escribir_json(ruta, lista)

    def escribir_catalogo(self, productos):
        _escribir_json(self.raiz / "catalogo.json", productos)

    # ---- limpieza ----
    def limpiar(self, producto, extension, dias):
        """Borra archivos con esa extensión más viejos que `dias` días.
        Los índices no se tocan: el visor muestra "imagen no disponible"."""
        if not dias:
            return 0
        limite = datetime.now(timezone.utc) - timedelta(days=dias)
        borrados = 0
        base = self.raiz / producto
        for ruta in base.glob(f"*/*/*/*.{extension}"):
            try:
                anio, mes, dia = (int(p) for p in ruta.parts[-4:-1])
            except ValueError:
                continue
            if datetime(anio, mes, dia, tzinfo=timezone.utc) < limite:
                ruta.unlink()
                borrados += 1
        if borrados:
            log.info("%s: borrados %d archivos .%s de más de %d días", producto, borrados, extension, dias)
        return borrados

    def espacio_libre_gb(self):
        return shutil.disk_usage(self.raiz).free / 1e9


def _leer_json(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def _escribir_json(ruta, datos):
    """Escritura atómica: se escribe a un temporal y se renombra. Así el visor
    nunca lee un JSON a medio escribir."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ruta)
