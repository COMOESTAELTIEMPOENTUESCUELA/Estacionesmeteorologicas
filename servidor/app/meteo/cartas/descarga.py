"""
Tarea "cartas" del servicio de ingesta: baja las cartas nuevas y las archiva.
Lo ya guardado no se vuelve a pedir; entre imagen e imagen se espera un poco
para no cargar los servidores ajenos.
"""
import fcntl
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

from . import archivo, bentley, ogimet

log = logging.getLogger(__name__)
PAUSA_SEG = 1.0
# Ogimet: una consulta "pesada" cada 20 s por IP (ver ingesta/fuentes/ogimet.py).
PAUSA_OGIMET_SEG = 21
ESPERAS_OGIMET = (30, 90)
# En la vuelta de cada hora se miran las últimas horas sinópticas. Se deja
# un margen para que Ogimet ya tenga los SYNOP de esa hora.
# OJO: Ogimet solo genera estos mapas para el último día, aproximadamente
# (probado el 12/10/2026: hoy sí; hace 2 días o una semana, respuesta
# vacía). No se puede rellenar el historial: hay que archivarlos a diario.
OGIMET_HORAS_ATRAS = 24
OGIMET_MARGEN = timedelta(hours=2)


class EnCurso(Exception):
    pass


class NoDisponible(Exception):
    """Ogimet respondió vacío: esa hora ya no está disponible (no es un rechazo)."""


@contextmanager
def una_sola_descarga(raiz):
    """Evita dos descargas a la vez (ej. el servicio y un 'una cartas' a
    mano): pidiéndole los dos a Ogimet, se pasan de su límite de consultas."""
    Path(raiz, "cartas").mkdir(parents=True, exist_ok=True)
    with open(Path(raiz, "cartas", ".descargando"), "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EnCurso("ya hay otra descarga de cartas en curso (por ejemplo, la del servicio); probá en unos minutos")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def correr(cfg, raiz, sesion):
    with una_sola_descarga(raiz):
        return _correr(cfg, raiz, sesion)


def _correr(cfg, raiz, sesion):
    horas = tuple(cfg.get("horas_utc", (0, 6, 12, 18)))
    activos = [p for p in cfg["productos"] if p.get("activo", True)]
    nuevas = 0
    corridas = {}
    for p in activos:
        if p["fuente"] == "ogimet":
            continue      # se baja abajo, todas juntas (una página trae todos los mapas)
        if p["fuente"] != "bentley":
            log.info("cartas %s: fuente %s todavía no implementada", p["id"], p["fuente"])
            continue
        if p["dominio"] not in corridas:
            r = sesion.get(bentley.url_pagina(p["dominio"], p["variable"]), timeout=60)
            r.raise_for_status()
            corridas[p["dominio"]] = bentley.leer_corrida(r.text)
        corrida = corridas[p["dominio"]]
        for n, valida in bentley.cuadros_analisis(corrida, tuple(p.get("horas_utc", horas))):
            if archivo.existe(raiz, p["id"], valida):
                continue
            url = bentley.url_imagen(p["dominio"], p["variable"], n)
            try:
                r = sesion.get(url, timeout=60)
                r.raise_for_status()
                if not bentley.es_de_esta_corrida(r.headers, corrida["inicio"]):
                    log.info("cartas %s: la página ya dice corrida %s pero las imágenes son anteriores; "
                             "se reintenta en la próxima vuelta", p["id"], corrida["inicio"])
                    break
                archivo.guardar(raiz, p["id"], valida, r.content)
                nuevas += 1
            except Exception as ex:
                log.warning("cartas %s %s: %s", p["id"], valida, str(ex)[:150])
            time.sleep(PAUSA_SEG)
    ahora = datetime.now(timezone.utc) - OGIMET_MARGEN
    desde = ahora - timedelta(hours=OGIMET_HORAS_ATRAS)
    nuevas += _correr_ogimet(cfg, raiz, sesion, desde, ahora)
    log.info("cartas: %d nuevas", nuevas)
    return nuevas


def horas_sinopticas(desde, hasta, horas_utc):
    t = desde.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    while t <= hasta:
        if t.hour in horas_utc:
            yield t
        t += timedelta(hours=1)


def _pedir_ogimet(sesion, zona, ts):
    """Página de mapas de esa hora -> {código: imagen}. Si Ogimet rechaza
    la consulta (límite de una cada 20 s) responde una página SIN mapas:
    eso se toma como error y se reintenta, no como "no hay mapa"."""
    for i in range(len(ESPERAS_OGIMET) + 1):
        try:
            r = sesion.get(ogimet.URL_PAGINA, params=ogimet.parametros(zona, ts), timeout=120)
            r.raise_for_status()
            if not r.text.strip():
                raise NoDisponible(f"Ogimet ya no tiene los mapas de {ts:%d/%m %H} UTC (solo guarda el último día)")
            imgs = ogimet.imagenes(r.text)
            if not imgs:
                aviso = "límite de consultas" if "quota" in r.text.lower() else "página sin mapas"
                raise RuntimeError(f"Ogimet no devolvió mapas ({aviso})")
            return imgs
        except NoDisponible:
            raise                   # reintentar no sirve
        except Exception as ex:
            if i == len(ESPERAS_OGIMET):
                raise
            log.info("cartas ogimet %s: %s; reintento en %d s", ts, str(ex)[:100], ESPERAS_OGIMET[i])
            time.sleep(ESPERAS_OGIMET[i])


def correr_ogimet(cfg, raiz, sesion, desde, hasta):
    """Mapas de Ogimet entre desde y hasta (también sirve para el historial)."""
    with una_sola_descarga(raiz):
        return _correr_ogimet(cfg, raiz, sesion, desde, hasta)


def _correr_ogimet(cfg, raiz, sesion, desde, hasta):
    def horas_de(p):
        return tuple(p.get("horas_utc", cfg.get("horas_utc", (0, 6, 12, 18))))
    productos = [p for p in cfg["productos"] if p["fuente"] == "ogimet" and p.get("activo", True)]
    horas = tuple(sorted({h for p in productos for h in horas_de(p)}))
    nuevas = 0
    for zona in sorted({p["zona"] for p in productos}):
        de_zona = [p for p in productos if p["zona"] == zona]
        for ts in horas_sinopticas(desde, hasta, horas):
            faltan = [p for p in de_zona if ts.hour in horas_de(p) and not archivo.existe(raiz, p["id"], ts)]
            if not faltan:
                continue
            try:
                imgs = _pedir_ogimet(sesion, zona, ts)
            except NoDisponible as ex:
                log.info("cartas: %s", ex)
                time.sleep(PAUSA_OGIMET_SEG)
                continue
            except Exception as ex:
                log.warning("cartas ogimet %s: %s", ts, str(ex)[:150])
                time.sleep(PAUSA_OGIMET_SEG)
                continue
            for p in faltan:
                img = imgs.get(p["codigo"])
                if not img:
                    log.warning("cartas %s %s: la página no trae el mapa %s", p["id"], ts, p["codigo"])
                    continue
                if img["ts"] != ts:   # el nombre del archivo trae la fecha: tiene que ser la pedida
                    log.warning("cartas %s: se pidió %s y Ogimet dio %s; no se guarda", p["id"], ts, img["ts"])
                    continue
                try:
                    r = sesion.get(img["url"], timeout=60)
                    r.raise_for_status()
                    archivo.guardar(raiz, p["id"], ts, r.content)
                    nuevas += 1
                except Exception as ex:
                    log.warning("cartas %s %s: %s", p["id"], ts, str(ex)[:150])
                time.sleep(PAUSA_SEG)
            time.sleep(PAUSA_OGIMET_SEG)
    return nuevas
