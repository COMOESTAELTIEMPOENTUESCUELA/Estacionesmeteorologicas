"""
Orquestación: qué se trae, de qué período, y cada cuánto.

Cada vuelta pide una VENTANA hacia atrás más larga que la frecuencia
(ej. cada 15 min se piden las últimas 6 h). Como el guardado es idempotente,
lo repetido no duplica nada, y si la PC estuvo apagada o una estación
transmitió tarde, se completa solo en la vuelta siguiente.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import yaml

from . import db
from .base import sesion_http
from .fuentes import comunidad, davis, ogimet, pronosticos, thingspeak, wunderground

log = logging.getLogger(__name__)

RECOLECTORES = {
    "thingspeak": thingspeak,
    "wunderground": wunderground,
    "davis": davis,
    "ogimet": ogimet,
}

# Cuánto hacia atrás pide cada fuente en el funcionamiento normal.
VENTANA = {
    "thingspeak": timedelta(hours=6),
    "wunderground": timedelta(hours=26),  # se piden días completos (hoy y ayer)
    "davis": timedelta(hours=12),
    "ogimet": timedelta(hours=30),        # los SYNOP a veces llegan tarde
}


def cargar_config(ruta):
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Ingesta:
    def __init__(self, cfg, conn):
        self.cfg = cfg
        self.conn = conn
        self.sesion = sesion_http()

    def estaciones(self, fuente=None, ids=None, incluir_inactivas=False):
        for e in self.cfg["estaciones"]:
            if fuente and e["fuente"] != fuente:
                continue
            if ids and e["id"] not in ids:
                continue
            if not incluir_inactivas and not e.get("activa", True):
                continue
            yield e

    # ----- observaciones de estaciones -----
    def correr_fuente(self, fuente, desde, hasta, ids=None, incluir_inactivas=False):
        mod = RECOLECTORES[fuente]
        total = 0
        for e in self.estaciones(fuente, ids, incluir_inactivas):
            try:
                with db.registrar_tarea(self.conn, f"{fuente}:{e['id']}") as reg:
                    resultado = mod.traer(e, desde, hasta, self.sesion)
                    if fuente == "ogimet":
                        obs, crudos = resultado
                        db.guardar_synop_crudo(self.conn, crudos)
                    else:
                        obs = resultado
                    # Variables que la estación no mide de verdad (ej. un
                    # anemómetro que no existe y el archivo rellena con 0).
                    ignorar = set((e.get("config") or {}).get("ignorar_variables", []))
                    if ignorar:
                        obs = [o for o in obs if o.variable not in ignorar]
                    reemplazar = wunderground.dias_a_reemplazar(e["id"], obs) if fuente == "wunderground" else None
                    reg["filas"] = db.guardar_observaciones(self.conn, obs, fuente, reemplazar)
                    total += reg["filas"]
                    log.info("%s %s: %d observaciones leídas, %d nuevas o cambiadas",
                             fuente, e["id"], len(obs), reg["filas"])
            except Exception as ex:
                # Una estación caída no frena a las demás.
                log.warning("%s %s: falló (%s)", fuente, e["id"], ex)
        return total

    # ----- comunidad y pronósticos -----
    def correr_comunidad(self):
        with db.registrar_tarea(self.conn, "comunidad") as reg:
            reportes = comunidad.traer(self.cfg["comunidad"], self.sesion)
            reg["filas"] = comunidad.guardar(self.conn, reportes)
            log.info("comunidad: %d reportes leídos", len(reportes))

    def correr_pronosticos(self):
        with db.registrar_tarea(self.conn, "pronosticos") as reg:
            emisiones = pronosticos.traer(self.cfg["pronosticos"], self.sesion)
            reg["filas"] = pronosticos.guardar(self.conn, emisiones)
            log.info("pronosticos: %d emisiones nuevas", reg["filas"])

    def correr(self, tarea, desde=None, hasta=None, ids=None, incluir_inactivas=False):
        hasta = hasta or datetime.now(timezone.utc)
        if tarea in RECOLECTORES:
            desde = desde or hasta - VENTANA[tarea]
            return self.correr_fuente(tarea, desde, hasta, ids, incluir_inactivas)
        if tarea == "comunidad":
            return self.correr_comunidad()
        if tarea == "pronosticos":
            return self.correr_pronosticos()
        raise ValueError(f"Tarea desconocida: {tarea}")

    # ----- loop permanente -----
    def asegurar_conexion(self):
        """Si la base se reinició, la conexión vieja queda cortada y TODO
        fallaría (incluso escribir la bitácora). Se reconecta antes de cada tarea."""
        if not db.conexion_sana(self.conn):
            log.warning("Conexión con la base perdida: reconectando")
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = db.conectar()

    def para_siempre(self, limite_sin_progreso_min=30):
        frecuencias = self.cfg.get("frecuencias_min", {})
        proxima = {t: 0.0 for t in frecuencias}
        self.ultimo_progreso = time.time()
        iniciar_guardian(self, limite_sin_progreso_min * 60)
        while True:
            ahora = time.time()
            for tarea, minutos in frecuencias.items():
                if ahora >= proxima[tarea]:
                    proxima[tarea] = ahora + minutos * 60
                    try:
                        self.asegurar_conexion()
                        self.correr(tarea)
                    except Exception as ex:
                        # Queda registrado en ingesta_log; en el log, una línea alcanza.
                        log.warning("Tarea %s falló: %s", tarea, str(ex)[:300])
                    self.ultimo_progreso = time.time()
            time.sleep(20)


def iniciar_guardian(ingesta, limite_seg):
    """Perro guardián: un hilo aparte que mira si el loop sigue avanzando. Si
    pasa `limite_seg` sin que termine ninguna tarea (algo quedó colgado), cierra
    el proceso; Docker lo vuelve a levantar (restart: unless-stopped)."""
    def vigilar():
        while True:
            time.sleep(60)
            quieto = time.time() - ingesta.ultimo_progreso
            if quieto > limite_seg:
                log.critical("Sin progreso hace %.0f min: reiniciando el proceso", quieto / 60)
                logging.shutdown()
                os._exit(1)
    threading.Thread(target=vigilar, daemon=True, name="guardian").start()
