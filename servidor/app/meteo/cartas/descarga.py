"""
Tarea "cartas" del servicio de ingesta: baja las cartas nuevas y las archiva.
Lo ya guardado no se vuelve a pedir; entre imagen e imagen se espera un poco
para no cargar los servidores ajenos.
"""
import fcntl
import hashlib
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


def correr(cfg, raiz, sesion, tormenta=None):
    """tormenta(desde, hasta, region) -> lista de informes de tormenta (SYNOP):
    la da la ingesta, que tiene la base. Sin eso, no se arman los mapas de rayos."""
    with una_sola_descarga(raiz):
        return _correr(cfg, raiz, sesion, tormenta)


def _correr(cfg, raiz, sesion, tormenta=None):
    horas = tuple(cfg.get("horas_utc", (0, 6, 12, 18)))
    # descargar: false -> se muestra lo archivado, pero no se baja más (ej. Bentley).
    activos = [p for p in cfg["productos"] if p.get("activo", True) and p.get("descargar", True)]
    nuevas = 0
    corridas = {}
    errores = []        # una fuente caída no frena a las otras: se informa al final
    for p in activos:
        if p["fuente"] == "ogimet":
            continue      # se baja abajo, todas juntas (una página trae todos los mapas)
        if p["fuente"] == "star":
            try:
                nuevas += _correr_star(p, raiz, sesion, tuple(p.get("horas_utc", horas)))
            except Exception as ex:
                log.warning("cartas %s: %s", p["id"], str(ex)[:150])
            continue
        if p["fuente"] == "glm":
            nuevas += _correr_rayos(p, raiz, sesion, tuple(p.get("horas_utc", horas)), tormenta)
            continue
        if p["fuente"] in ("gfs", "gfs_pronostico", "gfs_analisis"):
            continue      # se dibujan abajo, todas juntas (un solo archivo del GFS para las cuatro)
        if p["fuente"] != "bentley":
            log.info("cartas %s: fuente %s todavía no implementada", p["id"], p["fuente"])
            continue
        if p["dominio"] not in corridas:
            try:
                r = sesion.get(bentley.url_pagina(p["dominio"], p["variable"]), timeout=60)
                r.raise_for_status()
                corridas[p["dominio"]] = bentley.leer_corrida(r.text)
            except Exception as ex:
                # Se intenta una sola vez por vuelta (no una por carta: cada
                # intento fallido puede tardar un minuto en rendirse).
                corridas[p["dominio"]] = None
                errores.append(f"Bentley ({p['dominio']}) no respondió: {str(ex)[:120]}")
                log.warning("cartas: %s", errores[-1])
        corrida = corridas[p["dominio"]]
        if corrida is None:
            continue
        cuadros = bentley.cuadros_analisis(corrida, tuple(p.get("horas_utc", horas)))
        vistas = set()        # huellas de lo bajado en esta vuelta
        for n, valida in cuadros:
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
                # 28/10/2026: la página copió una imagen vieja en TODOS los
                # cuadros. Una carta idéntica a otra no es de esa hora.
                huella = hashlib.md5(r.content).hexdigest()
                if huella in vistas or _igual_a_otra_hora(raiz, p["id"], valida, huella):
                    log.warning("cartas %s %s: la página trae una imagen repetida; no se guarda", p["id"], valida)
                    vistas.add(huella)
                    continue
                vistas.add(huella)
                archivo.guardar(raiz, p["id"], valida, r.content)
                nuevas += 1
            except Exception as ex:
                log.warning("cartas %s %s: %s", p["id"], valida, str(ex)[:150])
            time.sleep(PAUSA_SEG)
        if p.get("pronostico", True):
            try:
                _correr_pronostico_bentley(p, corrida, raiz, sesion)
            except Exception as ex:
                errores.append(f"pronóstico {p['id']}: {str(ex)[:120]}")
                log.warning("cartas: %s", errores[-1])
    for fuente in ("gfs", "gfs_analisis"):
        gfs_p = [p for p in activos if p["fuente"] == fuente]
        if gfs_p:
            try:
                nuevas += _correr_gfs(gfs_p, raiz, sesion, horas)
            except Exception as ex:
                errores.append(f"cartas GFS: {str(ex)[:150]}")
                log.warning("cartas: %s", errores[-1])
    if cfg.get("sondeos"):
        try:
            _correr_sondeos(cfg["sondeos"], raiz, sesion)
        except Exception as ex:
            errores.append(f"radiosondeos: {str(ex)[:150]}")
            log.warning("cartas: %s", errores[-1])
    ahora = datetime.now(timezone.utc) - OGIMET_MARGEN
    desde = ahora - timedelta(hours=OGIMET_HORAS_ATRAS)
    nuevas += _correr_ogimet(cfg, raiz, sesion, desde, ahora)
    # Las archivadas antes de que existieran las miniaturas (o si alguna falló).
    mins = archivo.completar_miniaturas(raiz, [p["id"] for p in activos if p["fuente"] != "gfs_pronostico"])
    log.info("cartas: %d nuevas%s", nuevas, f", {mins} miniaturas completadas" if mins else "")
    # Al final: cuando hay corrida nueva tarda ~15-25 min (132 mapas), y así no demora lo demás.
    prono = [p for p in activos if p["fuente"] == "gfs_pronostico"]
    if prono:
        try:
            _correr_pronostico_gfs(prono, raiz, sesion, analisis=[p for p in activos if p["fuente"] == "gfs_analisis"])
        except Exception as ex:
            errores.append(f"pronóstico GFS: {str(ex)[:150]}")
            log.warning("cartas: %s", errores[-1])
    if errores:
        # Lo demás ya se bajó; esto deja la falla a la vista en "Estado de la red".
        raise RuntimeError(f"{nuevas} cartas nuevas, pero: " + "; ".join(errores))
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


REINTENTOS_SEG = (5, 15)    # esperas antes de volver a pedir una imagen que falló


def _pedir_con_reintentos(sesion, url):
    """GET que reintenta si se corta la conexión o el servidor responde con
    error (el de Bentley a veces corta alguna en medio de una tanda de 128)."""
    for espera in (*REINTENTOS_SEG, None):
        try:
            r = sesion.get(url, timeout=60)
            r.raise_for_status()
            return r
        except Exception as ex:
            if espera is None:
                raise
            log.info("cartas: %s falló (%s); reintento en %d s", url.rsplit("/", 1)[-1], str(ex)[:80], espera)
            time.sleep(espera)


def _igual_a_otra_hora(raiz, producto, ts, huella, horas=(6, 12, 18, 24)):
    """¿La carta archivada de alguna de las horas anteriores (o siguientes)
    es exactamente esta imagen?"""
    for h in horas:
        for otra in (ts - timedelta(hours=h), ts + timedelta(hours=h)):
            base = archivo.ruta(raiz, producto, otra)
            for ext in ("png", "gif", "jpg"):
                f = base.with_suffix(f".{ext}")
                if f.exists() and hashlib.md5(f.read_bytes()).hexdigest() == huella:
                    return True
    return False


def _tiene_repetidas(carpeta, guardado):
    """¿Hay dos plazos guardados con exactamente la misma imagen?"""
    huellas = set()
    for c in guardado["cuadros"]:
        try:
            h = hashlib.md5((carpeta / c["archivo"]).read_bytes()).hexdigest()
        except FileNotFoundError:
            continue
        if h in huellas:
            return True
        huellas.add(h)
    return False


MIN_PLAZOS_CORRIDA_NUEVA = 8     # +48 h: una corrida nueva más corta que esto no reemplaza a la anterior


def _correr_pronostico_bentley(p, corrida, raiz, sesion):
    """Pronóstico de la última corrida de Bentley, todos los plazos que estén
    publicados. Se arma en una carpeta aparte y recién al final reemplaza a lo
    anterior (nunca se ve a medias).

    Si la corrida nueva tiene publicados solo los primeros plazos (los últimos
    cuadros todavía son de la corrida anterior: Bentley no terminó, o esa
    corrida no llega a 8 días), se guardan los que hay y en cada vuelta se
    intenta completar el resto SIN volver a bajar lo que ya se tiene.
    Si dos plazos traen exactamente la misma imagen, no son pronósticos de
    verdad (el 29/10/2026 la página devolvió para los 31 cuadros una imagen
    vieja del 20/10): esos se descartan y se corta ahí.
    Devuelve cuántas imágenes nuevas bajó."""
    import shutil
    from . import pronostico
    cuadros = bentley.cuadros_pronostico(corrida)
    guardado = pronostico.leer(raiz, p["id"])
    if guardado and _tiene_repetidas(pronostico.carpeta(raiz, p["id"]), guardado):
        log.warning("cartas %s: el pronóstico guardado tiene imágenes repetidas; se borra", p["id"])
        shutil.rmtree(pronostico.carpeta(raiz, p["id"]), ignore_errors=True)
        guardado = None
    misma = bool(guardado) and datetime.fromisoformat(guardado["inicio"]) == corrida["inicio"]
    ya = {}
    if misma:
        if len(guardado["cuadros"]) >= len(cuadros):
            return 0                          # completa: nada que hacer
        ya = {c["plazo_h"]: c["archivo"] for c in guardado["cuadros"]}
    tmp = pronostico.empezar(raiz, p["id"])
    guardados, nuevos, huellas = [], 0, {}
    try:
        for n, valida, plazo in cuadros:
            nom = pronostico.nombre(p["id"], corrida["inicio"], plazo)
            if plazo in ya:                   # de esta misma corrida, ya bajado
                shutil.copy2(pronostico.carpeta(raiz, p["id"]) / ya[plazo], tmp / nom)
                huellas.setdefault(hashlib.md5((tmp / nom).read_bytes()).hexdigest(), plazo)
                guardados.append((plazo, valida, nom))
                continue
            r = _pedir_con_reintentos(sesion, bentley.url_imagen(p["dominio"], p["variable"], n))
            if not bentley.es_de_esta_corrida(r.headers, corrida["inicio"], sin_fecha=False):
                log.info("cartas %s: corrida %s publicada hasta +%d h; el resto se intenta en la próxima vuelta",
                         p["id"], corrida["inicio"], plazo - 6)
                break
            if archivo.tipo_imagen(r.content) != "png":
                raise ValueError(f"el cuadro {n} no es una imagen")
            huella = hashlib.md5(r.content).hexdigest()
            if huella in huellas:
                # Repetida: ni esta ni la anterior igual son de verdad.
                repetido = huellas[huella]
                log.warning("cartas %s: el plazo +%d h es la misma imagen que +%d h; se descartan desde +%d h",
                            p["id"], plazo, repetido, repetido)
                for pl, _, nm in [g for g in guardados if g[0] >= repetido]:
                    (tmp / nm).unlink(missing_ok=True)
                guardados = [g for g in guardados if g[0] < repetido]
                nuevos = min(nuevos, len(guardados))
                break
            huellas[huella] = plazo
            (tmp / nom).write_bytes(r.content)
            guardados.append((plazo, valida, nom))
            nuevos += 1
            time.sleep(PAUSA_SEG)
    except Exception:
        pronostico.descartar(tmp)
        raise
    if not nuevos or (not misma and len(guardados) < MIN_PLAZOS_CORRIDA_NUEVA):
        pronostico.descartar(tmp)             # nada nuevo, o muy poco: queda lo que había
        return 0
    pronostico.publicar(raiz, p["id"], tmp, corrida["inicio"], guardados, esperados=len(cuadros))
    log.info("cartas %s: pronóstico de la corrida %s, %d de %d plazos", p["id"], corrida["inicio"],
             len(guardados), len(cuadros))
    return nuevos


def _bajar_gfs(ts, pedidos, region, sesion, plazo=0):
    """gfs.bajar con reintentos: NOAA (o la red) a veces corta una conexión
    en medio de los ~100 pedidos de un archivo. "Todavía no publicado"
    (FileNotFoundError) no se reintenta."""
    from . import gfs
    for espera in (*REINTENTOS_SEG, None):
        try:
            return gfs.bajar(ts, pedidos, region, sesion, plazo=plazo) if plazo else gfs.bajar(ts, pedidos, region, sesion)
        except FileNotFoundError:
            raise
        except Exception as ex:
            if espera is None:
                raise
            log.info("cartas: GFS %s +%d h falló (%s); reintento en %d s", ts, plazo, str(ex)[:80], espera)
            time.sleep(espera)


SONDEOS_CADA = timedelta(hours=6)     # IGRA se actualiza una vez por día: no hace falta mirar cada hora


def _correr_sondeos(estaciones, raiz, sesion, ahora=None):
    """Radiosondeos reales (IGRA, ver sondeos.py): cada 6 h se baja el
    archivo del año de cada estación y se guardan los sondeos que falten.
    Devuelve cuántos sondeos nuevos guardó."""
    from . import sondeos
    ahora = ahora or datetime.now(timezone.utc)
    marca = Path(raiz) / "cartas" / sondeos.CARPETA / "ultima_descarga"
    try:
        if ahora - datetime.fromisoformat(marca.read_text().strip()) < SONDEOS_CADA:
            return 0
    except (FileNotFoundError, ValueError):
        pass
    nuevos = 0
    for e in estaciones:
        for s in sondeos.bajar_igra(e["igra"], sesion):
            if not sondeos.ruta(raiz, e["id"], s["ts"]).exists():
                sondeos.guardar(raiz, e["id"], s)
                nuevos += 1
    marca.parent.mkdir(parents=True, exist_ok=True)
    marca.write_text(ahora.isoformat())
    log.info("cartas: %d radiosondeos nuevos", nuevos)
    return nuevos


GFS_PRONO_CORRIDAS_ATRAS = 4    # si la PC estuvo apagada, no se buscan corridas de hace más de un día


def _ultima_corrida_gfs(sesion, plazo_final, ahora=None):
    """Inicio de la corrida más nueva del GFS que ya está completa en NOAA (ya
    está el último plazo; los plazos salen en orden), o None."""
    from . import gfs
    ahora = ahora or datetime.now(timezone.utc)
    t = ahora.replace(hour=ahora.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for _ in range(GFS_PRONO_CORRIDAS_ATRAS):
        if sesion.get(gfs.urls(t, plazo=plazo_final)[0] + ".idx", timeout=30).status_code == 200:
            return t
        t -= timedelta(hours=6)
    return None


GFS_PRONO_CONSERVAR = timedelta(days=8, hours=6)   # el +192 h de la corrida más vieja ya pasó: se puede comparar con lo que pasó


def _correr_pronostico_gfs(productos, raiz, sesion, ahora=None, analisis=()):
    """Pronóstico del GFS dibujado en el servidor con el formato de las cartas
    de Bentley (estilo_bentley.py): todos los plazos de la última corrida.
    La nueva se arma aparte y reemplaza a la anterior al terminar; las de los
    últimos 8 días quedan en el historial (pronostico.py), para comparar
    corridas entre sí y el pronóstico con lo que pasó.
    analisis: productos (fuente gfs_analisis) donde se ARCHIVA para siempre el
    +0 h de cada corrida, como las cartas de análisis de cada hora.
    Devuelve cuántas imágenes dibujó."""
    from . import estilo_bentley as eb, gfs, pronostico
    inicio = _ultima_corrida_gfs(sesion, eb.PLAZOS[-1], ahora)
    if inicio is None:
        return 0
    def al_dia(p):
        d = pronostico.leer(raiz, p["id"])
        return d and datetime.fromisoformat(d["inicio"]) >= inicio and len(d["cuadros"]) >= len(eb.PLAZOS)
    if all(al_dia(p) for p in productos):
        return 0
    log.info("cartas: pronóstico GFS de la corrida %s, dibujando %d plazos", inicio, len(eb.PLAZOS))
    tmps = {p["id"]: pronostico.empezar(raiz, p["id"]) for p in productos}
    cuadros = {p["id"]: [] for p in productos}
    try:
        for plazo in eb.PLAZOS:
            campos = _bajar_gfs(inicio, eb.PEDIDOS, eb.REGION, sesion, plazo=plazo)
            imagenes = eb.dibujar(campos, inicio, plazo)
            if plazo == 0:
                for p in analisis:
                    if p.get("carta") in imagenes and not archivo.existe(raiz, p["id"], inicio):
                        archivo.guardar(raiz, p["id"], inicio, imagenes[p["carta"]])
            for p in productos:
                nom = pronostico.nombre(p["id"], inicio, plazo)
                (tmps[p["id"]] / nom).write_bytes(imagenes[p["carta"]])
                cuadros[p["id"]].append((plazo, inicio + timedelta(hours=plazo), nom))
    except Exception:
        for t in tmps.values():
            pronostico.descartar(t)        # queda la corrida anterior; se reintenta en la próxima vuelta
        raise
    extra = {"marco": eb.marco()}
    for p in productos:
        pronostico.publicar(raiz, p["id"], tmps[p["id"]], inicio, cuadros[p["id"]],
                            conservar=GFS_PRONO_CONSERVAR, ahora=ahora, extra=extra)
    log.info("cartas: pronóstico GFS de la corrida %s listo (%d plazos)", inicio, len(eb.PLAZOS))
    return len(eb.PLAZOS) * len(productos)


GFS_HORAS_ATRAS = 48      # el análisis sale ~4 h después de la hora; si la PC estuvo apagada, se recupera


def _correr_gfs(productos, raiz, sesion, horas, desde=None, hasta=None):
    """Cartas de diagnóstico propias con el ANÁLISIS del GFS (el +0 h de cada
    corrida): un solo archivo por hora dibuja las cuatro. NOAA guarda los
    análisis desde 2021: con desde/hasta se pueden dibujar eventos viejos.
    Dos formatos: fuente "gfs_analisis" (el de las cartas de Bentley, igual
    que el pronóstico: estilo_bentley.py) y fuente "gfs" (el anterior, con
    proyección cónica: diagnostico.py)."""
    from . import diagnostico, estilo_bentley, gfs, puntos
    hasta = hasta or datetime.now(timezone.utc)
    desde = desde or hasta - timedelta(hours=GFS_HORAS_ATRAS)
    # Con las de formato Bentley se guardan además los valores del análisis en
    # cada ciudad (meteograma de diagnóstico y sondeos: puntos.py).
    con_puntos = productos[0]["fuente"] == "gfs_analisis"
    if con_puntos:
        pedidos, region = estilo_bentley.PEDIDOS | puntos.PEDIDOS, estilo_bentley.REGION
        dibujar = lambda campos, ts: estilo_bentley.dibujar(campos, ts, 0)
    else:
        pedidos, region = diagnostico.PEDIDOS, tuple(productos[0].get("region", (-95, -25, -60, 5)))
        dibujar = lambda campos, ts: diagnostico.dibujar(campos, ts, region)
    nuevas = 0
    for ts in horas_sinopticas(desde, hasta, horas):
        faltan = [p for p in productos if not archivo.existe(raiz, p["id"], ts)]
        faltan_puntos = con_puntos and not puntos.existe(raiz, ts)
        if not faltan and not faltan_puntos:
            continue
        try:
            campos = _bajar_gfs(ts, pedidos, region, sesion)
        except FileNotFoundError as ex:
            log.info("cartas: %s", ex)
            continue
        if faltan_puntos:
            puntos.guardar(raiz, ts, puntos.extraer(campos))
        if faltan:
            dibujos = dibujar(campos, ts)
            for p in faltan:
                archivo.guardar(raiz, p["id"], ts, dibujos[p["carta"]])
                nuevas += 1
        log.info("cartas GFS %s: %d dibujadas%s", ts, len(faltan), ", valores en las ciudades" if faltan_puntos else "")
    return nuevas


RAYOS_HORAS_ATRAS = 48                 # el SYNOP con la tormenta puede llegar tarde
RAYOS_DEMORA = timedelta(minutes=20)   # NOAA publica el GLM y el canal 13 con unos minutos de atraso


def _correr_rayos(p, raiz, sesion, horas_utc, tormenta, desde=None, hasta=None, forzar=False):
    """Mapa de rayos (GLM sobre infrarrojo) de cada hora de carta en la que
    el SYNOP de alguna estación de la región informó tormenta. Con forzar,
    de todas las horas del período (para dibujar un evento a pedido)."""
    from . import rayos
    if tormenta is None and not forzar:
        return 0
    ultimo = datetime.now(timezone.utc) - RAYOS_DEMORA
    hasta = min(hasta or ultimo, ultimo)
    desde = desde or hasta - timedelta(hours=RAYOS_HORAS_ATRAS)
    region = tuple(p["region"])
    minutos = int(p.get("minutos", 60))
    nuevas = 0
    for ts in horas_sinopticas(desde, hasta, horas_utc):
        if archivo.existe(raiz, p["id"], ts):
            continue
        # La tormenta de los 60 min previos aparece en el SYNOP de esa hora o
        # en el de la hora siguiente (ww 29 / 91-94: "en la hora anterior").
        informes = tormenta(ts - timedelta(minutes=minutos), ts + timedelta(hours=1), region) if tormenta else []
        if not informes and not forzar:
            continue
        try:
            lista = rayos.destellos(ts - timedelta(minutes=minutos), ts, region, sesion)
            rec = rayos.infrarrojo(ts, region, sesion)
            png = rayos.dibujar(rec, lista, ts, minutos, region, p.get("titulo_region", ""),
                                p.get("puntos", []), ", ".join(informes[:3]))
            archivo.guardar(raiz, p["id"], ts, png)
            rayos.guardar_destellos(raiz, p["id"], ts, lista)
            nuevas += 1
            log.info("cartas %s %s: %d destellos (%s)", p["id"], ts, len(lista), ", ".join(informes[:3]) or "a pedido")
        except Exception as ex:
            log.warning("cartas %s %s: %s", p["id"], ts, str(ex)[:200])
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
                # Respuesta vacía: el mapa de esa hora todavía no está armado (tarda
                # un rato después de la hora), o faltó el Referer. No es un error:
                # la tarea de cada hora lo vuelve a pedir.
                raise NoDisponible(f"Ogimet todavía no tiene los mapas de {ts:%d/%m %H} UTC (se reintenta más tarde)")
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


def correr_historico(cfg, raiz, sesion, desde, hasta, tormenta=None, forzar=False):
    """Rellenar un período: satélite (NOAA guarda unos 10 días) y mapas de
    Ogimet (los genera para fechas pasadas; si responde vacío para una hora,
    se saltea y se sigue). Bentley se completa solo en la vuelta normal (su
    página tiene 14 días). Los rayos (GLM): de las horas con tormenta en el
    SYNOP, o de todas con forzar (NOAA guarda el GLM para siempre).
    Devuelve {producto: nuevas}."""
    horas = tuple(cfg.get("horas_utc", (0, 6, 12, 18)))
    salida = {}
    with una_sola_descarga(raiz):
        for p in cfg["productos"]:
            if p.get("activo", True) and p.get("descargar", True) and p["fuente"] == "star":
                try:
                    salida[p["id"]] = _correr_star(p, raiz, sesion, tuple(p.get("horas_utc", horas)), desde, hasta)
                except Exception as ex:
                    log.warning("cartas %s: %s", p["id"], str(ex)[:150])
            if p.get("activo", True) and p.get("descargar", True) and p["fuente"] == "glm":
                salida[p["id"]] = _correr_rayos(p, raiz, sesion, tuple(p.get("horas_utc", horas)),
                                                tormenta, desde, hasta, forzar)
        for fuente in ("gfs", "gfs_analisis"):
            gfs_p = [p for p in cfg["productos"] if p.get("activo", True) and p.get("descargar", True)
                     and p["fuente"] == fuente]
            if gfs_p:
                try:
                    salida[fuente] = _correr_gfs(gfs_p, raiz, sesion, horas, desde, hasta)
                except Exception as ex:
                    log.warning("cartas GFS: %s", str(ex)[:150])
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
