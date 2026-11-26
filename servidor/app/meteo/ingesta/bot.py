"""
"Bot observador": mira los últimos datos de las estaciones y, si detecta un
cambio brusco, publica una nota en la red del grupo (⚡ Me llamó la
atención, #alertanowcasting) para que el grupo entre a comentar.

Reglas (cada una con su ventana):
- la PRESIÓN baja 3 hPa o más en 3 horas;
- la TEMPERATURA baja 4 °C o más en 1 hora;
- una RÁFAGA de 60 km/h o más en la última hora;
- el SYNOP informa TORMENTA (tiempo presente 13, 17, 29, 91-99).

Presión y temperatura: solo las sinópticas del SMN y el Observatorio (las
EMAs de las escuelas a veces tienen saltos que no son reales). Ráfagas: todas
las que las miden. Como mucho UNA alerta por estación cada 3 horas, y una
sola nota por vuelta con todas las estaciones que saltaron.

Cada aviso lleva una IMAGEN según lo que se detectó (IMAGENES_POR_TIPO):
tormenta o ráfaga -> el mapa de rayos del GLM (o el infrarrojo); caída de
temperatura o de presión -> la carta de análisis (o el satélite). Las cartas se
archivan en las horas sinópticas y llegan con demora: se elige la más cercana
a la hora del aviso que ya esté, y si todavía no hay ninguna, el bot la
completa en las vueltas siguientes (hasta IMAGEN_ESPERA).

Corre como tarea de la ingesta ("bot", cada 15 minutos).
"""
import os
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..cartas.rayos import WW_TORMENTA
from ..ciudades import SYNOP_PUNTOS

log = logging.getLogger(__name__)

EMAIL_BOT = "bot.observador@local"         # no es un mail de Google: nadie puede ingresar con él
NOMBRE_BOT = "Bot Zari"                       # una zarigüeya que mira el tiempo (su foto: web/static/iconos/zari.webp)
FOTO_BOT = Path(__file__).resolve().parents[1] / "web" / "static" / "iconos" / "zari.webp"
CAIDA_PRESION_HPA, HORAS_PRESION = 3.0, 3
CAIDA_TEMP_C = 4.0
RAFAGA_KMH = 60.0
SILENCIO = timedelta(hours=3)                # entre dos alertas de la misma estación
OBSERVATORIO = ("observatorio_campbell", "observatorio_davis")
ARG = timezone(timedelta(hours=-3))
CIUDAD_DE = {v: k for k, v in SYNOP_PUNTOS.items()} | {e: "La Plata" for e in OBSERVATORIO}

num = lambda v, d=1: f"{v:.{d}f}".replace(".", ",")

# Qué imagen mostrar según lo detectado (la primera que esté archivada).
IMAGENES_POR_TIPO = {
    "tormenta": ["satelite_rayos_bsas", "satelite_glm", "satelite_ir13"],
    "rafaga": ["satelite_rayos_bsas", "satelite_ir13", "satelite_geocolor"],
    "temperatura": ["gfs_ana_mslp_jet", "satelite_ir13"],
    "presion": ["gfs_ana_mslp_jet", "satelite_airmass", "satelite_ir13"],
}
PRIORIDAD = ("tormenta", "rafaga", "temperatura", "presion")      # si saltaron varias cosas, manda la primera
IMAGEN_ESPERA = timedelta(hours=8)          # hasta cuándo se sigue buscando la imagen de un aviso
IMAGEN_DISTANCIA = timedelta(hours=4)       # la imagen tiene que ser de hasta 4 h antes o después del aviso


def tipo_de(frase):
    """La frase de detectar() -> "tormenta" | "rafaga" | "temperatura" | "presion"."""
    for clave, tipo in (("tormenta", "tormenta"), ("ráfaga", "rafaga"), ("temperatura", "temperatura"), ("presión", "presion")):
        if clave in frase:
            return tipo
    return None


def imagenes_para(frases):
    """Los productos a probar, en orden, según lo más importante detectado."""
    tipos = {tipo_de(f) for f in frases}
    salida = []
    for t in PRIORIDAD:
        if t in tipos:
            salida += [p for p in IMAGENES_POR_TIPO[t] if p not in salida]
    return salida


def elegir_imagen(raiz, productos, alerta):
    """La carta archivada para el aviso: el primer producto de la lista que
    tenga alguna imagen a menos de IMAGEN_DISTANCIA de la hora del aviso, la
    más cercana. -> (producto, ts) o None si todavía no llegó ninguna."""
    from ..cartas import archivo
    t0 = alerta.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    horas = sorted({t0 + timedelta(hours=h) for h in range(-int(IMAGEN_DISTANCIA.total_seconds() // 3600),
                                                            int(IMAGEN_DISTANCIA.total_seconds() // 3600) + 1)},
                   key=lambda t: (abs(t - alerta), t < alerta))       # a igual distancia, mejor la de después
    horas = [t for t in horas if abs(t - alerta) <= IMAGEN_DISTANCIA]
    for p in productos:
        for t in horas:
            if archivo.existe(raiz, p, t):
                return p, t
    return None


def _cerca(serie, objetivo, tolerancia):
    """El dato de la serie [(ts, valor)] más cercano a `objetivo`, dentro de la tolerancia."""
    mejor = None
    for ts, v in serie:
        d = abs((ts - objetivo).total_seconds())
        if d <= tolerancia.total_seconds() and (mejor is None or d < mejor[0]):
            mejor = (d, ts, v)
    return mejor[1:] if mejor else None


def detectar(e, ahora):
    """e: {"id", "nombre", "tipo", "temp": [(ts, v)], "pres": [...], "racha": [...] (m/s), "ww": [...]}
    -> lista de frases (vacía si no pasó nada)."""
    salida = []
    confiable = e["tipo"] == "sinoptica" or e["id"] in OBSERVATORIO
    hora = lambda ts: ts.astimezone(ARG).strftime("%H:%M")
    if confiable and e.get("pres"):
        ts1, p1 = max(e["pres"])
        if ahora - ts1 <= timedelta(minutes=90):
            antes = _cerca(e["pres"], ts1 - timedelta(hours=HORAS_PRESION), timedelta(minutes=35))
            if antes and antes[1] - p1 >= CAIDA_PRESION_HPA:
                salida.append(f"la presión bajó {num(antes[1] - p1)} hPa en {HORAS_PRESION} h ({num(antes[1])} → {num(p1)} hPa, a las {hora(ts1)})")
    if confiable and e.get("temp"):
        ts1, t1 = max(e["temp"])
        if ahora - ts1 <= timedelta(minutes=70):
            antes = _cerca(e["temp"], ts1 - timedelta(hours=1), timedelta(minutes=15))
            if antes and antes[1] - t1 >= CAIDA_TEMP_C:
                salida.append(f"la temperatura bajó {num(antes[1] - t1)} °C en 1 h ({num(antes[1])} → {num(t1)} °C, a las {hora(ts1)})")
    rafagas = [(v, ts) for ts, v in e.get("racha", []) if ahora - ts <= timedelta(minutes=60)]
    if rafagas and max(rafagas)[0] * 3.6 >= RAFAGA_KMH:
        v, ts = max(rafagas)
        salida.append(f"ráfaga de {num(v * 3.6, 0)} km/h a las {hora(ts)}")
    tormenta = [ts for ts, v in e.get("ww", []) if int(v) in WW_TORMENTA and ahora - ts <= timedelta(minutes=70)]
    if tormenta:
        salida.append(f"el SYNOP informa tormenta (a las {hora(max(tormenta))})")
    return salida


def _usuario_bot(cur):
    cur.execute("""INSERT INTO usuario (email, nombre, rol) VALUES (%s, %s, 'pronosticador')
                   ON CONFLICT (email) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id""", (EMAIL_BOT, NOMBRE_BOT))
    bot = cur.fetchone()["id"]
    # Su foto de perfil (la zarigüeya): se pone o se cambia si el dibujo es otro.
    try:
        foto = FOTO_BOT.read_bytes()
        with cur.connection.transaction():        # punto de guardado: si falla, no arruina la vuelta del bot
            cur.execute("""INSERT INTO usuario_foto (usuario_id, datos) VALUES (%s, %s)
                           ON CONFLICT (usuario_id) DO UPDATE SET datos = EXCLUDED.datos, actualizada = now()
                           WHERE usuario_foto.datos IS DISTINCT FROM EXCLUDED.datos""", (bot, foto))
    except (OSError, psycopg.errors.UndefinedTable):
        pass                                  # sin el dibujo o sin la tabla: el círculo con la inicial
    return bot


def _poner_imagen(contexto):
    """Si ya hay una carta para el aviso, la nota pasa a apuntar a ella: su
    imagen se ve en la tarjeta y tocarla abre esa carta en el visor."""
    raiz = os.environ.get("ARCHIVO_DIR", "/archivo")
    alerta = datetime.fromisoformat(contexto["alerta"])
    r = elegir_imagen(raiz, contexto.get("imagenes") or [], alerta)
    if not r:
        return False
    producto, ts = r
    contexto.update({"seccion": "analisis", "producto": producto, "ts": ts.isoformat()})
    return True


def completar_imagenes(cur, bot, ahora):
    """Avisos recientes del bot que todavía esperan su imagen (llega con demora)."""
    cur.execute("""SELECT id, contexto FROM nota WHERE usuario_id = %s AND creada > %s
                   AND contexto ? 'imagenes' AND NOT contexto ? 'producto'""", (bot, ahora - IMAGEN_ESPERA))
    for r in cur.fetchall():
        c = dict(r["contexto"])
        if _poner_imagen(c):
            cur.execute("UPDATE nota SET contexto = %s WHERE id = %s", (Jsonb(c), r["id"]))
            log.info("bot observador: nota %s con imagen %s %s", r["id"], c["producto"], c["ts"])


def correr(conn, ahora=None):
    """Una vuelta: lee las últimas 4 h, detecta y publica (si corresponde). -> nota creada o None."""
    ahora = ahora or datetime.now(timezone.utc)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT to_regclass('nota') IS NOT NULL AS hay")
        if not cur.fetchone()["hay"]:          # la web todavía no creó las tablas de la red
            conn.rollback()
            return None
        cur.execute("""
            SELECT o.estacion_id, e.nombre, e.tipo, o.variable, o.ts, o.valor
            FROM observacion o JOIN estacion e ON e.id = o.estacion_id
            WHERE e.activa AND o.ts > %s AND o.ts <= %s AND o.variable IN ('temp', 'pnm', 'pres_est', 'racha', 'ww')""",
                    (ahora - timedelta(hours=4), ahora))
        est = {}
        for r in cur.fetchall():
            e = est.setdefault(r["estacion_id"], {"id": r["estacion_id"], "nombre": r["nombre"], "tipo": r["tipo"],
                                                  "temp": [], "pnm": [], "pres_est": [], "racha": [], "ww": []})
            e[r["variable"]].append((r["ts"], r["valor"]))
        for e in est.values():
            e["pres"] = e["pnm"] or e["pres_est"]          # la que tenga (no mezclar las dos)
        bot = _usuario_bot(cur)
        completar_imagenes(cur, bot, ahora)
        cur.execute("SELECT contexto->>'estaciones' AS e FROM nota WHERE usuario_id = %s AND creada > %s", (bot, ahora - SILENCIO))
        recientes = {x for r in cur.fetchall() for x in (r["e"] or "").split(",") if x}
        hallazgos = [(e, f) for e in sorted(est.values(), key=lambda x: x["nombre"])
                     if e["id"] not in recientes for f in [detectar(e, ahora)] if f]
        if not hallazgos:
            conn.commit()
            return None
        lineas = [f"• {e['nombre']}: " + "; ".join(f) + "." for e, f in hallazgos]
        texto = (f"🔎 Cambio brusco detectado ({ahora.astimezone(ARG):%d/%m %H:%M} hs):\n" + "\n".join(lineas)
                 + "\n\nPuede ser el paso de un frente o una tormenta. ¿Alguien lo está mirando? (aviso automático)")
        primera = hallazgos[0][0]["id"]
        contexto = {"seccion": "meteograma", "ts": ahora.isoformat(), "estaciones": ",".join(e["id"] for e, _ in hallazgos)}
        if CIUDAD_DE.get(primera):
            contexto["ciudad"] = CIUDAD_DE[primera]
        # La imagen según lo detectado: si ya está, va; si no, se completa en otra vuelta.
        contexto["imagenes"] = imagenes_para([f for _, fs in hallazgos for f in fs])
        contexto["alerta"] = ahora.isoformat()
        _poner_imagen(contexto)
        cur.execute("""INSERT INTO nota (usuario_id, tipo, texto, etiquetas, compartida, fecha_evento, contexto, creada, actualizada)
                       VALUES (%s, 'llamo_atencion', %s, %s, true, %s, %s, %s, %s) RETURNING id""",
                    (bot, texto, ["alertanowcasting"], ahora.astimezone(ARG).date(), Jsonb(contexto), ahora, ahora))
        nid = cur.fetchone()["id"]
    conn.commit()
    log.info("bot observador: nota %s (%s)", nid, ", ".join(e["nombre"] for e, _ in hallazgos))
    return nid
