"""
Ogimet: mensajes SYNOP crudos de las estaciones del SMN.

    https://www.ogimet.com/cgi-bin/getsynop?block=87593&begin=202610100000&end=202610110000

A diferencia de los scripts de Pronostico-unlp (que leen la tabla HTML ya
decodificada por Ogimet), acá se pide el MENSAJE ORIGINAL y se decodifica
en casa (ver synop.py). El mensaje se guarda tal cual en la tabla
synop_crudo: si mañana se mejora el decodificador, se reprocesa todo sin
volver a pedir nada.

Ogimet bloquea un rato a quien hace muchos pedidos seguidos: por eso se
espera entre estaciones y se pide de a una ventana de días por vez.
"""
import logging
import time

from .. import synop

log = logging.getLogger(__name__)

URL = "https://www.ogimet.com/cgi-bin/getsynop"
PAUSA_SEG = 8
# Ogimet a veces responde 504 (Gateway Time-out) cuando está cargado: se
# reintenta la misma ventana esperando cada vez más.
ESPERAS_REINTENTO = (30, 90)


def _pedir_ventana(estacion, ini, fin, sesion):
    for intento in range(len(ESPERAS_REINTENTO) + 1):
        try:
            r = sesion.get(URL, params={
                "block": estacion["omm"],
                "begin": ini.strftime("%Y%m%d%H%M"),
                "end": fin.strftime("%Y%m%d%H%M"),
            }, timeout=90)
            r.raise_for_status()
            if "Status: 500" in r.text or "excess" in r.text.lower():
                raise RuntimeError("Ogimet rechazó el pedido (posible límite de consultas)")
            return r.text
        except Exception as ex:
            if intento == len(ESPERAS_REINTENTO):
                raise
            espera = ESPERAS_REINTENTO[intento]
            log.info("ogimet %s %s: %s; reintento en %d s", estacion["id"], ini.date(), str(ex)[:120], espera)
            time.sleep(espera)


def traer(estacion, desde, hasta, sesion):
    """Devuelve (observaciones, mensajes_crudos).

    Si una ventana sigue fallando después de los reintentos, se sigue con
    las demás (antes se perdía la estación entera por una sola semana). Lo
    que falte se completa volviendo a correr el mismo comando: lo que ya
    está guardado no se duplica. Si fallan TODAS, es un error."""
    obs, crudos, fallidas = [], [], []
    ventanas = list(synop.ventanas(desde, hasta, dias=7))
    for ini, fin in ventanas:
        try:
            texto = _pedir_ventana(estacion, ini, fin, sesion)
        except Exception as ex:
            fallidas.append(f"{ini:%d/%m}-{fin:%d/%m} ({str(ex)[:80]})")
            time.sleep(PAUSA_SEG)
            continue
        for indicativo, ts, mensaje in synop.parsear_getsynop(texto):
            if indicativo != estacion["omm"]:
                continue
            crudos.append((estacion["id"], ts, mensaje))
            obs += synop.decodificar(mensaje, ts, estacion["id"])
        time.sleep(PAUSA_SEG)
    if fallidas and len(fallidas) == len(ventanas):
        raise RuntimeError("todas las ventanas fallaron: " + "; ".join(fallidas[:3]))
    if fallidas:
        log.warning("ogimet %s: quedaron sin traer %s. Volvé a correr el mismo período para completarlas.",
                    estacion["id"], ", ".join(fallidas))
    return obs, crudos
