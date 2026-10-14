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

from . import archivo, bentley, ogimet, star

log = logging.getLogger(__name__)
PAUSA_SEG = 1.0
# Ogimet: una consulta "pesada" cada 20 s por IP (ver ingesta/fuentes/ogimet.py).
PAUSA_OGIMET_SEG = 21
ESPERAS_OGIMET = (30, 90)
# En la vuelta de cada hora se miran las últimas horas sinópticas. Se deja
# un margen para que Ogimet ya tenga los SYNOP de esa hora.
# Ogimet genera estos mapas también para fechas pasadas (el 12/10/2026 una
# prueba dio respuestas vacías para fechas viejas, pero fue por cruzarse con
# las consultas del servicio: el usuario las vio en la página). En la vuelta
# normal alcanza con el último día; para rellenar: historico cartas.
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
        if p["fuente"] == "star":
            try:
                nuevas += _correr_star(p, raiz, sesion, tuple(p.get("horas_utc", horas)))
            except Exception as ex:
                log.warning("cartas %s: %s", p["id"], str(ex)[:150])
            continue
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
    # Las archivadas antes de que existieran las miniaturas (o si alguna falló).
    mins = archivo.completar_miniaturas(raiz, [p["id"] for p in activos])
    log.info("cartas: %d nuevas%s", nuevas, f", {mins} miniaturas completadas" if mins else "")
    return nuevas


# NOAA STAR guarda las imágenes con fecha unos pocos días: se miran las
# últimas 48 horas (si la PC estuvo apagada un día, se recupera).
STAR_HORAS_ATRAS = 48


def _correr_star(p, raiz, sesion, horas_utc, desde=None, hasta=None):
    """Satélite (NOAA STAR): la imagen más cercana a cada hora de las cartas
    (las últimas STAR_HORAS_ATRAS horas, o de desde a hasta)."""
    hasta = hasta or datetime.now(timezone.utc)
    desde = desde or hasta - timedelta(hours=STAR_HORAS_ATRAS)
    faltan = [ts for ts in horas_sinopticas(desde, hasta, horas_utc)
              if not archivo.existe(raiz, p["id"], ts)]
    if not faltan:
        return 0
    r = sesion.get(star.url_carpeta(p), timeout=60)
    r.raise_for_status()
    disponibles = star.imagenes(r.text, p.get("tamano", "1800x1080"))
    nuevas = 0
    for ts in faltan:
        nombre = star.elegir(disponibles, ts)
        if not nombre:
            continue          # todavía no salió (o ya no está): se reintenta en la próxima vuelta
        try:
            img = sesion.get(star.url_carpeta(p) + nombre, timeout=60)
            img.raise_for_status()
            archivo.guardar(raiz, p["id"], ts, img.content)
            nuevas += 1
        except Exception as ex:
            log.warning("cartas %s %s: %s", p["id"], ts, str(ex)[:150])
        time.sleep(PAUSA_SEG)
    return nuevas


def horas_sinopticas(desde, hasta, horas_utc):
    t = desde.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    while t <= hasta:
        if t.hour in horas_utc:
            yield t
        t += timedelta(hours=1)


def nombre_fondo(zona):
    return f"ogimet_{zona}"


def _bajar_fondo(raiz, sesion, zona, html):
    """La geografía que va debajo de los mapas (una vez por zona)."""
    if archivo.fondo(raiz, nombre_fondo(zona)):
        return
    candidatos = ogimet.fondos(html, zona)
    for url in candidatos:
        try:
            r = sesion.get(url, timeout=60)
            r.raise_for_status()
            archivo.guardar_fondo(raiz, nombre_fondo(zona), r.content)
            log.info("cartas: fondo de Ogimet %s guardado (%s)", zona, url)
            return
        except Exception as ex:
            log.info("cartas: fondo %s: %s", url, str(ex)[:100])
    log.warning("cartas: no encontré la imagen de fondo de Ogimet %s (probados: %s). Correr "
                "'python -m meteo.ingesta diagnostico-ogimet' y mandar la salida", zona, candidatos or "ninguno")


def _pedir_ogimet(sesion, zona, ts):
    """Página de mapas de esa hora -> ({código: imagen}, html). Si Ogimet rechaza
    la consulta (límite de una cada 20 s) responde una página SIN mapas:
    eso se toma como error y se reintenta, no como "no hay mapa"."""
    for i in range(len(ESPERAS_OGIMET) + 1):
        try:
            r = sesion.get(ogimet.URL_PAGINA, params=ogimet.parametros(zona, ts), timeout=120,
                           headers=ogimet.ENCABEZADOS)
            r.raise_for_status()
            if not r.text.strip():
                raise NoDisponible(f"Ogimet ya no tiene los mapas de {ts:%d/%m %H} UTC (solo guarda el último día)")
            imgs = ogimet.imagenes(r.text)
            if not imgs:
                aviso = "límite de consultas" if "quota" in r.text.lower() else "página sin mapas"
                raise RuntimeError(f"Ogimet no devolvió mapas ({aviso})")
            return imgs, r.text
        except NoDisponible:
            raise                   # reintentar no sirve
        except Exception as ex:
            if i == len(ESPERAS_OGIMET):
                raise
            log.info("cartas ogimet %s: %s; reintento en %d s", ts, str(ex)[:100], ESPERAS_OGIMET[i])
            time.sleep(ESPERAS_OGIMET[i])


def correr_ogimet(cfg, raiz, sesion, desde, hasta):
    """Mapas de Ogimet entre desde y hasta."""
    with una_sola_descarga(raiz):
        return _correr_ogimet(cfg, raiz, sesion, desde, hasta)


def correr_historico(cfg, raiz, sesion, desde, hasta):
    """Rellenar un período: satélite (NOAA guarda unos 10 días) y mapas de
    Ogimet (los genera para fechas pasadas; si responde vacío para una hora,
    se saltea y se sigue). Bentley se completa solo en la vuelta normal (su
    página tiene 14 días). Devuelve {producto: nuevas}."""
    horas = tuple(cfg.get("horas_utc", (0, 6, 12, 18)))
    salida = {}
    with una_sola_descarga(raiz):
        for p in cfg["productos"]:
            if p.get("activo", True) and p["fuente"] == "star":
                try:
                    salida[p["id"]] = _correr_star(p, raiz, sesion, tuple(p.get("horas_utc", horas)), desde, hasta)
                except Exception as ex:
                    log.warning("cartas %s: %s", p["id"], str(ex)[:150])
        # Ojo: una consulta "pesada" cada 20 s por IP. Con el servicio corriendo
        # a la vez se cruzan; por eso una_sola_descarga() bloquea la otra.
        salida["ogimet"] = _correr_ogimet(cfg, raiz, sesion, desde, hasta)
    return salida


def _correr_ogimet(cfg, raiz, sesion, desde, hasta):
    def horas_de(p):
        return tuple(p.get("horas_utc", cfg.get("horas_utc", (0, 6, 12, 18))))
    productos = [p for p in cfg["productos"] if p["fuente"] == "ogimet" and p.get("activo", True)]
    horas = tuple(sorted({h for p in productos for h in horas_de(p)}))
    nuevas = 0
    for zona in sorted({p["zona"] for p in productos}):
        de_zona = [p for p in productos if p["zona"] == zona]
        for ts in horas_sinopticas(desde, hasta, horas):
            # Falta la imagen, o sus zonas clickeables (las archivadas antes de que existieran).
            faltan = [p for p in de_zona if ts.hour in horas_de(p)
                      and (not archivo.existe(raiz, p["id"], ts) or not archivo.ruta_areas(raiz, p["id"], ts).exists())]
            if not faltan:
                continue
            try:
                imgs, html = _pedir_ogimet(sesion, zona, ts)
                zonas = ogimet.areas(html)
            except NoDisponible as ex:
                log.info("cartas: %s", ex)
                time.sleep(PAUSA_OGIMET_SEG)
                continue
            except Exception as ex:
                log.warning("cartas ogimet %s: %s", ts, str(ex)[:150])
                time.sleep(PAUSA_OGIMET_SEG)
                continue
            try:
                _bajar_fondo(raiz, sesion, zona, html)
            except Exception as ex:
                log.warning("cartas: fondo de Ogimet: %s", str(ex)[:150])
            for p in faltan:
                if imgs.get(p["codigo"], {}).get("ts") == ts:
                    # Aunque venga vacío: así no se vuelve a pedir la página por las zonas.
                    archivo.guardar_areas(raiz, p["id"], ts, zonas.get(p["codigo"], []))
                if archivo.existe(raiz, p["id"], ts):
                    continue          # la imagen ya estaba: solo faltaban las zonas
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


def diagnostico(raiz, sesion, zona="amersurs"):
    """Pide la página de Ogimet de la última hora sinóptica y cuenta qué
    imágenes trae y si los mapas son transparentes. Guarda el HTML en
    cartas/_diag/ para poder mirarlo. Devuelve líneas de texto."""
    ts = datetime.now(timezone.utc) - OGIMET_MARGEN
    ts = ts.replace(hour=ts.hour - ts.hour % 6, minute=0, second=0, microsecond=0)
    r = sesion.get(ogimet.URL_PAGINA, params=ogimet.parametros(zona, ts), timeout=120, headers=ogimet.ENCABEZADOS)
    r.raise_for_status()
    carpeta = Path(raiz, "cartas", "_diag")
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "ogimet.html").write_text(r.text, encoding="utf-8")
    lineas = [f"Página de {ts:%d/%m %H} UTC: {len(r.text)} caracteres (copia en {carpeta / 'ogimet.html'})",
              "Imágenes que nombra la página:"]
    lineas += [f"  {u}" for u in ogimet.referencias(r.text)] or ["  (ninguna)"]
    imgs = ogimet.imagenes(r.text)
    if "Te" in imgs:
        time.sleep(PAUSA_SEG)
        datos = sesion.get(imgs["Te"]["url"], timeout=60).content
        lineas.append(f"Mapa Te: {len(datos)} bytes, transparente: {'sí' if ogimet.es_transparente(datos) else 'no'}")
    lineas.append(f"Candidatos a fondo: {ogimet.fondos(r.text, zona) or 'ninguno'}")
    f = archivo.fondo(raiz, nombre_fondo(zona))
    lineas.append(f"Fondo guardado: {f or 'todavía no'}")
    return lineas
