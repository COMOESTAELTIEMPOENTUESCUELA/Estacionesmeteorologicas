"""
Tarea "cartas" del servicio de ingesta: baja las cartas nuevas y las archiva.
Lo ya guardado no se vuelve a pedir; entre imagen e imagen se espera un poco
para no cargar los servidores ajenos.
"""
import logging
import time

from . import archivo, bentley

log = logging.getLogger(__name__)
PAUSA_SEG = 1.0


def correr(cfg, raiz, sesion):
    horas = tuple(cfg.get("horas_utc", (0, 6, 12, 18)))
    activos = [p for p in cfg["productos"] if p.get("activo", True)]
    nuevas = 0
    corridas = {}
    for p in activos:
        if p["fuente"] != "bentley":
            log.info("cartas %s: fuente %s todavía no implementada", p["id"], p["fuente"])
            continue
        if p["dominio"] not in corridas:
            r = sesion.get(bentley.url_pagina(p["dominio"], p["variable"]), timeout=60)
            r.raise_for_status()
            corridas[p["dominio"]] = bentley.leer_corrida(r.text)
        corrida = corridas[p["dominio"]]
        for n, valida in bentley.cuadros_analisis(corrida, horas):
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
    log.info("cartas: %d nuevas", nuevas)
    return nuevas
