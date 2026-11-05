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

Corre como tarea de la ingesta ("bot", cada 15 minutos).
"""
import logging
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..cartas.rayos import WW_TORMENTA
from ..ciudades import SYNOP_PUNTOS

log = logging.getLogger(__name__)

EMAIL_BOT = "bot.observador@local"         # no es un mail de Google: nadie puede ingresar con él
NOMBRE_BOT = "🤖 Bot observador"
CAIDA_PRESION_HPA, HORAS_PRESION = 3.0, 3
CAIDA_TEMP_C = 4.0
RAFAGA_KMH = 60.0
SILENCIO = timedelta(hours=3)                # entre dos alertas de la misma estación
OBSERVATORIO = ("observatorio_campbell", "observatorio_davis")
ARG = timezone(timedelta(hours=-3))
CIUDAD_DE = {v: k for k, v in SYNOP_PUNTOS.items()} | {e: "La Plata" for e in OBSERVATORIO}

num = lambda v, d=1: f"{v:.{d}f}".replace(".", ",")


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
    return cur.fetchone()["id"]


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
        cur.execute("SELECT contexto->>'estaciones' AS e FROM nota WHERE usuario_id = %s AND creada > %s", (bot, ahora - SILENCIO))
        recientes = {x for r in cur.fetchall() for x in (r["e"] or "").split(",") if x}
        hallazgos = [(e, f) for e in sorted(est.values(), key=lambda x: x["nombre"])
                     if e["id"] not in recientes for f in [detectar(e, ahora)] if f]
        if not hallazgos:
            conn.commit()
            return None
        lineas = [f"• {e['nombre']}: " + "; ".join(f) + "." for e, f in hallazgos]
        texto = (f"🤖 Cambio brusco detectado ({ahora.astimezone(ARG):%d/%m %H:%M} hs):\n" + "\n".join(lineas)
                 + "\n\nPuede ser el paso de un frente o una tormenta. ¿Alguien lo está mirando? (aviso automático)")
        primera = hallazgos[0][0]["id"]
        contexto = {"seccion": "meteograma", "ts": ahora.isoformat(), "estaciones": ",".join(e["id"] for e, _ in hallazgos)}
        if CIUDAD_DE.get(primera):
            contexto["ciudad"] = CIUDAD_DE[primera]
        cur.execute("""INSERT INTO nota (usuario_id, tipo, texto, etiquetas, compartida, fecha_evento, contexto, creada, actualizada)
                       VALUES (%s, 'llamo_atencion', %s, %s, true, %s, %s, %s, %s) RETURNING id""",
                    (bot, texto, ["alertanowcasting"], ahora.astimezone(ARG).date(), Jsonb(contexto), ahora, ahora))
        nid = cur.fetchone()["id"]
    conn.commit()
    log.info("bot observador: nota %s (%s)", nid, ", ".join(e["nombre"] for e, _ in hallazgos))
    return nid
