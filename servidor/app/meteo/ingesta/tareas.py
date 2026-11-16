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

import psycopg
import yaml

from . import db
from ..verificacion import observado
from .base import HORA_ARG, sesion_http
from .fuentes import comunidad, davis, municipio_lp, ogimet, pronosticos, thingspeak, wunderground

log = logging.getLogger(__name__)

def clave_a_corrida(clave):
    """"2026111012" -> datetime UTC (o None si no es una corrida)."""
    try:
        t = datetime.strptime(str(clave), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return t if t.hour % 6 == 0 else None


RECOLECTORES = {
    "thingspeak": thingspeak,
    "wunderground": wunderground,
    "davis": davis,
    "ogimet": ogimet,
    "municipio_lp": municipio_lp,
}

# Cuánto hacia atrás pide cada fuente en el funcionamiento normal.
VENTANA = {
    "thingspeak": timedelta(hours=6),
    "wunderground": timedelta(hours=26),  # se piden días completos (hoy y ayer)
    "davis": timedelta(hours=12),
    "ogimet": timedelta(hours=30),        # los SYNOP a veces llegan tarde
    "municipio_lp": timedelta(hours=2),   # la API da solo el último valor (se pide cada 1 h)
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
                    # Lo que se calcula con lo ya guardado (ej. la lluvia a partir de un contador).
                    if hasattr(mod, "postproceso"):
                        obs = mod.postproceso(self.conn, e, obs)
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
            # Por si llegó una publicación con la fecha equivocada ya anotada.
            from . import correcciones
            correcciones.aplicar(self.conn, correcciones.cargar())

    def correr_verificacion(self, desde=None, hasta=None):
        """Recalcula lo observado de los últimos días (los SYNOP llegan
        tarde a veces, y la Davis/Campbell se completan en el día)."""
        with db.registrar_tarea(self.conn, "verificacion") as reg:
            hoy = datetime.now(HORA_ARG).date()
            # --desde 2026-01-01 llega como medianoche UTC: se usa la fecha tal cual.
            d = desde.date() if desde else hoy - timedelta(days=5)
            h = hasta.date() if hasta else hoy
            reg["filas"] = observado.calcular(self.conn, self.cfg["verificacion"], d, h)

    def correr_indices(self):
        """Índices climáticos (ENSO, SAM, MJO...): archivos chicos de NOAA y del BoM."""
        from .. import indices
        with db.registrar_tarea(self.conn, "indices") as reg:
            r = indices.bajar(os.environ.get("ARCHIVO_DIR", "/archivo"), self.sesion)
            reg["filas"] = sum(1 for s in r.values() if not s.startswith("FALLÓ"))
            log.info("indices: %s", "; ".join(f"{k}: {s}" for k, s in r.items()))

    def correr_bot(self):
        """El bot observador: si algo cambió de golpe, avisa en la red del grupo."""
        from . import bot
        with db.registrar_tarea(self.conn, "bot") as reg:
            reg["filas"] = 1 if bot.correr(self.conn) else 0

    def correr_cartas(self, desde=None, hasta=None):
        """Sin fechas: la vuelta normal. Con fechas: rellenar ese período (satélite y Ogimet)."""
        from ..cartas import descarga
        cfg = cargar_config(os.environ.get("CARTAS_CONFIG", "/config/cartas.yaml"))
        raiz = os.environ.get("ARCHIVO_DIR", "/archivo")
        try:
            self._cartas(descarga, cfg, raiz, desde, hasta)
        except descarga.EnCurso as ex:
            log.warning("cartas: %s", ex)

    def _cartas(self, descarga, cfg, raiz, desde, hasta):
        with db.registrar_tarea(self.conn, "cartas") as reg:
            if desde:
                r = descarga.correr_historico(cfg, raiz, self.sesion, desde, hasta,
                                              tormenta=self.hubo_tormenta, forzar=getattr(self, "forzar_rayos", False))
                log.info("cartas (histórico %s a %s): %s nuevas. Lo que NOAA ya borró "
                         "(guarda ~10 días) no se puede recuperar así.", desde, hasta, r)
                reg["filas"] = sum(r.values())
            else:
                reg["filas"] = descarga.correr(cfg, raiz, self.sesion, tormenta=self.hubo_tormenta,
                                               fijas=lambda: self.corridas_fijas(cfg, raiz))

    def corridas_fijas(self, cfg, raiz):
        """Corridas del GFS cuyas cartas de pronóstico NO se borran a los 8 días:
        - las fijadas a mano (📌) y las vueltas a dibujar a pedido;
        - las que usa alguna nota (la abrió desde el pronóstico GFS: su contexto
          dice qué corrida);
        - con `gfs_fijar_pronosticos` (cartas.yaml, sí por omisión), la que había
          cuando se publicó cada pronóstico del grupo (la que compara «🌐 GFS»:
          puntos.corrida_al_publicar, que si esa corrida no se dibujó cae a la anterior).
        Si una tabla no existe todavía (base sin cuentas), se sigue sin ella."""
        fijas = set()
        def consulta(sql, a_corrida):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql)
                    for (x,) in cur.fetchall():
                        c = a_corrida(x)
                        if c:
                            fijas.add(c)
            except psycopg.errors.UndefinedTable:
                pass
            finally:
                self.conn.rollback()           # no dejar la transacción abierta (idle_in_transaction)
        consulta("SELECT corrida FROM gfs_corrida WHERE fijada OR estado IS NOT NULL", lambda t: t.astimezone(timezone.utc))
        consulta("SELECT DISTINCT contexto->>'corrida' FROM nota WHERE contexto->>'corrida' ~ '^[0-9]{10}$'", clave_a_corrida)
        if cfg.get("gfs_fijar_pronosticos", True):
            from ..cartas import puntos
            consulta("SELECT DISTINCT capturado FROM pronostico_emision WHERE capturado > now() - interval '400 days'",
                     lambda cap: puntos.corrida_al_publicar(raiz, cap))
        return fijas

    def correr_gfs_pedidos(self):
        """Corridas viejas del GFS que alguien pidió volver a dibujar (🕓 en el
        pronóstico GFS): dibuja unos plazos de la más antigua pedida por vuelta."""
        from ..cartas import descarga
        try:
            with self.conn.cursor() as cur:
                cur.execute("""SELECT corrida, plazo FROM gfs_corrida WHERE estado IN ('pendiente', 'dibujando')
                               ORDER BY pedida LIMIT 1""")
                fila = cur.fetchone()
                if fila:
                    cur.execute("UPDATE gfs_corrida SET estado = 'dibujando', actualizada = now() WHERE corrida = %s", (fila[0],))
            self.conn.commit()
        except psycopg.errors.UndefinedTable:
            self.conn.rollback()
            return 0
        if not fila:
            return 0
        corrida, plazo = fila
        cfg = cargar_config(os.environ.get("CARTAS_CONFIG", "/config/cartas.yaml"))
        raiz = os.environ.get("ARCHIVO_DIR", "/archivo")
        estado, error, hechos, total = "dibujando", None, None, None
        with db.registrar_tarea(self.conn, "gfs_pedidos") as reg:
            try:
                hechos, total = descarga.dibujar_pedida(cfg, raiz, self.sesion, corrida, plazo)
                reg["filas"] = hechos
                if hechos >= total:
                    estado = "lista"
            except descarga.EnCurso:
                estado = "pendiente"                # la descarga normal está trabajando: en la próxima vuelta
            except FileNotFoundError:
                estado, error = "error", "NOAA no tiene esa corrida (guarda desde 2021)"
            except ValueError as ex:
                estado, error = "error", str(ex)[:300]
            except Exception as ex:
                estado, error = "error", f"falló la descarga o el dibujo: {str(ex)[:250]}"
                reg["mensaje"] = error
            with self.conn.cursor() as cur:
                cur.execute("""UPDATE gfs_corrida SET estado = %s, error = %s, hechos = COALESCE(%s, hechos),
                               actualizada = now() WHERE corrida = %s""", (estado, error, hechos, corrida))
            self.conn.commit()
        return hechos or 0

    def hubo_tormenta(self, desde, hasta, region):
        """Estaciones sinópticas de la región que informaron tormenta (tiempo
        presente) entre desde y hasta: ["La Plata Aero 95 (18 UTC)", ...]."""
        from ..cartas.rayos import WW_TORMENTA
        lon0, lon1, lat0, lat1 = region
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT e.nombre, o.ts, o.valor FROM observacion o JOIN estacion e ON e.id = o.estacion_id
                WHERE o.variable = 'ww' AND o.valor = ANY(%s) AND o.ts > %s AND o.ts <= %s
                  AND e.lon BETWEEN %s AND %s AND e.lat BETWEEN %s AND %s
                ORDER BY o.ts""", ([float(w) for w in WW_TORMENTA], desde, hasta, lon0, lon1, lat0, lat1))
            filas = cur.fetchall()
        # Cerrar la transacción que abrió la consulta: después vienen las cartas
        # (el pronóstico GFS tarda ~25 min) y la base corta una sesión que queda
        # más de 10 min "en transacción" sin hacer nada (idle_in_transaction).
        self.conn.rollback()
        return [f"{n} {int(v)} ({t.astimezone(timezone.utc):%H} UTC)" for n, t, v in filas]

    def correr(self, tarea, desde=None, hasta=None, ids=None, incluir_inactivas=False):
        hasta = hasta or datetime.now(timezone.utc)
        if tarea in RECOLECTORES:
            desde = desde or hasta - VENTANA[tarea]
            return self.correr_fuente(tarea, desde, hasta, ids, incluir_inactivas)
        if tarea == "comunidad":
            return self.correr_comunidad()
        if tarea == "pronosticos":
            return self.correr_pronosticos()
        if tarea == "cartas":
            return self.correr_cartas(desde, hasta if desde else None)
        if tarea == "gfs_pedidos":
            return self.correr_gfs_pedidos()
        if tarea == "indices":
            return self.correr_indices()
        if tarea == "bot":
            return self.correr_bot()
        if tarea == "verificacion":
            # Sin --desde/--hasta (servicio, "una"): los últimos días.
            return self.correr_verificacion(desde if desde else None, hasta if desde else None)
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
