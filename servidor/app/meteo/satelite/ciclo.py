"""
El "loop" del servicio: cada N minutos revisa si hay imágenes nuevas, las
procesa, actualiza los índices y limpia lo viejo.

Diseño pensado para una PC vieja que se puede apagar o quedar sin internet:
- Es IDEMPOTENTE: si una imagen ya está en disco, no la vuelve a procesar.
  Entonces correrlo dos veces, o después de un corte, nunca duplica nada.
- Un error en un producto (o en una imagen) se registra en el log y se sigue
  con el resto: una falla de NOAA no tira abajo todo el servicio.
"""
import hashlib
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
import yaml

from . import procesar, render, s3
from .archivo import Archivo

log = logging.getLogger(__name__)


def cargar_config(ruta):
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def elevacion_solar(t, lat, lon):
    """Altura del sol sobre el horizonte (grados), fórmula aproximada (±1°),
    suficiente para decidir si es de día."""
    dia = t.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(math.radians(360 / 365 * (dia - 81)))
    hora_solar = t.hour + t.minute / 60 + lon / 15
    angulo_horario = math.radians(15 * (hora_solar - 12))
    lat_r = math.radians(lat)
    seno = math.sin(lat_r) * math.sin(decl) + math.cos(lat_r) * math.cos(decl) * math.cos(angulo_horario)
    return math.degrees(math.asin(seno))


def es_de_dia(t, bbox, umbral=5.0):
    lat = (bbox[2] + bbox[3]) / 2
    lon = (bbox[0] + bbox[1]) / 2
    return elevacion_solar(t, lat, lon) > umbral


class Servicio:
    def __init__(self, config, raiz_archivo):
        self.cfg = config
        self.archivo = Archivo(raiz_archivo)
        self.sesion = requests.Session()
        self.sesion.headers["User-Agent"] = "servidor-meteo-escuelas/1.0"

    # ---------- productos GOES (dato numérico) ----------
    def procesar_archivo(self, prod, a, t):
        reg = self.cfg["regiones"][prod["region"]]
        bbox = tuple(reg["bbox"])
        png, nc = self.archivo.rutas(prod["id"], t)
        png.parent.mkdir(parents=True, exist_ok=True)
        inicio = time.time()
        rec = procesar.recortar(a.url(self.cfg["satelite"]["bucket"]), bbox, sesion=self.sesion)
        procesar.guardar_netcdf(rec, nc)
        render.dibujar(rec, png, bbox, prod["paleta"], reg["nombre"], self.cfg.get("puntos", []))
        self.archivo.registrar(prod["id"], t, {"imagen": png, "datos": nc})
        log.info("%s %s: listo (%.1f MB bajados, %.1f s)", prod["id"], t.strftime("%Y-%m-%d %H:%M"),
                 rec.bytes_bajados / 1e6, time.time() - inicio)

    def candidatos(self, prod, desde, hasta):
        """Archivos del bucket que corresponden a este producto y todavía no
        están procesados."""
        sat = self.cfg["satelite"]
        cada = prod.get("cada_min", 10)
        bbox = self.cfg["regiones"][prod["region"]]["bbox"]
        for a in s3.listar_rango(sat["bucket"], sat["producto"], prod["banda"], desde, hasta, self.sesion):
            t = s3.tiempo_nominal(a.inicio, 10)
            if t.minute % cada:
                continue
            if prod.get("solo_de_dia") and not es_de_dia(t, bbox):
                continue
            if self.archivo.ya_procesado(prod["id"], t):
                continue
            yield a, t

    def correr_productos(self, desde, hasta, solo=None):
        for prod in self.cfg.get("productos", []):
            if solo and prod["id"] not in solo:
                continue
            try:
                pendientes = list(self.candidatos(prod, desde, hasta))
            except Exception:
                log.exception("%s: no se pudo listar el bucket", prod["id"])
                continue
            for a, t in pendientes:
                try:
                    self.procesar_archivo(prod, a, t)
                except Exception:
                    log.exception("%s %s: falló el procesamiento", prod["id"], t)

    def redibujar(self, desde, hasta, solo=None):
        """Regenera los PNG a partir de los NetCDF ya guardados."""
        for prod in self.cfg.get("productos", []):
            if solo and prod["id"] not in solo:
                continue
            reg = self.cfg["regiones"][prod["region"]]
            dia = desde.replace(hour=0, minute=0, second=0, microsecond=0)
            while dia <= hasta:
                for nc in sorted(self.archivo.carpeta_dia(prod["id"], dia).glob("*.nc")):
                    rec = procesar.abrir_recorte(nc)
                    if desde <= rec.inicio <= hasta:
                        render.dibujar(rec, nc.with_suffix(".png"), tuple(reg["bbox"]), prod["paleta"],
                                       reg["nombre"], self.cfg.get("puntos", []))
                        log.info("redibujado %s", nc.with_suffix(".png").name)
                dia += timedelta(days=1)

    # ---------- imágenes listas (JPG de NOAA STAR) ----------
    def bajar_imagen_lista(self, img):
        r = self.sesion.get(img["url"], timeout=60)
        r.raise_for_status()
        # La hora de la imagen sale del header Last-Modified; si no viene, la
        # hora actual. Se redondea a la frecuencia del producto.
        try:
            t = parsedate_to_datetime(r.headers["Last-Modified"]).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            t = datetime.now(timezone.utc)
        t = s3.tiempo_nominal(t, img.get("cada_min", 10))
        ext = "jpg" if img["url"].lower().endswith((".jpg", ".jpeg")) else "png"
        ruta, _ = self.archivo.rutas(img["id"], t, ext)
        if ruta.exists():
            return
        # Si la imagen es idéntica a la última guardada (NOAA no actualizó),
        # no se guarda de nuevo.
        huella = hashlib.sha256(r.content).hexdigest()[:16]
        ultimas = self.archivo.raiz / img["id"] / "ultimas.json"
        if ultimas.exists() and huella in ultimas.read_text(encoding="utf-8"):
            return
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(r.content)
        self.archivo.registrar(img["id"], t, {"imagen": ruta}, extra={"sha": huella})
        log.info("%s %s: guardada", img["id"], t.strftime("%Y-%m-%d %H:%M"))

    def correr_imagenes_listas(self):
        for img in self.cfg.get("imagenes_listas", []):
            try:
                self.bajar_imagen_lista(img)
            except Exception as e:
                log.warning("%s: no se pudo bajar (%s)", img["id"], e)

    # ---------- mantenimiento ----------
    def escribir_catalogo(self):
        cat = [
            {"id": p["id"], "titulo": p.get("titulo", p["id"]), "tipo": "goes",
             "banda": p["banda"], "region": p["region"], "cada_min": p.get("cada_min", 10)}
            for p in self.cfg.get("productos", [])
        ] + [
            {"id": i["id"], "titulo": i.get("titulo", i["id"]), "tipo": "lista",
             "cada_min": i.get("cada_min", 10)}
            for i in self.cfg.get("imagenes_listas", [])
        ]
        self.archivo.escribir_catalogo(cat)

    def limpiar(self):
        for p in self.cfg.get("productos", []):
            self.archivo.limpiar(p["id"], "png", p.get("retener_png_dias"))
            self.archivo.limpiar(p["id"], "nc", p.get("retener_nc_dias"))
        for i in self.cfg.get("imagenes_listas", []):
            for ext in ("jpg", "png"):
                self.archivo.limpiar(i["id"], ext, i.get("retener_dias"))
        libre = self.archivo.espacio_libre_gb()
        if libre < 10:
            log.warning("¡Quedan solo %.1f GB libres en el disco del archivo!", libre)

    # ---------- loop principal ----------
    def una_vuelta(self):
        ahora = datetime.now(timezone.utc)
        desde = ahora - timedelta(hours=self.cfg.get("ventana_horas", 3))
        self.correr_productos(desde, ahora)
        self.correr_imagenes_listas()

    def para_siempre(self):
        self.escribir_catalogo()
        cada = self.cfg.get("revisar_cada_min", 5) * 60
        ultima_limpieza = None
        while True:
            inicio = time.time()
            self.una_vuelta()
            hoy = datetime.now(timezone.utc).date()
            if ultima_limpieza != hoy:
                self.limpiar()
                ultima_limpieza = hoy
            time.sleep(max(30, cada - (time.time() - inicio)))
