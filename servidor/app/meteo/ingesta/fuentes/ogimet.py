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
import time

from .. import synop

URL = "https://www.ogimet.com/cgi-bin/getsynop"
PAUSA_SEG = 8


def traer(estacion, desde, hasta, sesion):
    """Devuelve (observaciones, mensajes_crudos)."""
    obs, crudos = [], []
    for ini, fin in synop.ventanas(desde, hasta, dias=7):
        r = sesion.get(URL, params={
            "block": estacion["omm"],
            "begin": ini.strftime("%Y%m%d%H%M"),
            "end": fin.strftime("%Y%m%d%H%M"),
        }, timeout=90)
        r.raise_for_status()
        if "Status: 500" in r.text or "excess" in r.text.lower():
            raise RuntimeError("Ogimet rechazó el pedido (posible límite de consultas)")
        for indicativo, ts, mensaje in synop.parsear_getsynop(r.text):
            if indicativo != estacion["omm"]:
                continue
            crudos.append((estacion["id"], ts, mensaje))
            obs += synop.decodificar(mensaje, ts, estacion["id"])
        time.sleep(PAUSA_SEG)
    return obs, crudos
