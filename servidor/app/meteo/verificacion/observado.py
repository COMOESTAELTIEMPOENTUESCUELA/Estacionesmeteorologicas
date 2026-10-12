"""
Lo que REALMENTE pasó cada día, en los mismos términos en que se pronostica.

El día es el día CIVIL en hora argentina (00 a 24 hs). Por qué no el día
"de 9 a 9" del SMN: el pronóstico de "mínima del martes" puede referirse a
una mínima NOCTURNA (ej. después de un frente frío que pasa a la tarde, la
temperatura sigue bajando hasta la medianoche). Con el día civil, la mínima
del martes es la más baja de todo el martes, sea a las 6 o a las 23.

Además de los valores del día se guarda el detalle por período (madrugada,
mañana, tarde, noche) para poder verificar por período más adelante.

Las funciones de este archivo son "puras" (reciben listas, devuelven dicts):
se prueban sin base de datos. `calcular` es la única que lee y escribe.
"""
import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from ..ingesta.base import HORA_ARG

log = logging.getLogger(__name__)

# Un día cuenta como completo si hay datos en al menos 18 de sus 24 horas.
COMPLETITUD_MINIMA = 0.75
# Para el viento alcanza con 8 horas: varias estaciones del SMN (Bolívar,
# Tandil, Dolores) solo reportan de 09 a 21 hs, a veces con huecos, y lo que
# se pronostica es sobre todo el viento del día.
COMPLETITUD_VIENTO = 8 / 24

PERIODOS = (("madrugada", 0, 6), ("mañana", 6, 12), ("tarde", 12, 18), ("noche", 18, 24))

# Los grupos 1sTTT / 2sTTT de la sección 3 del SYNOP traen la máxima y la
# mínima de las 12 horas ANTERIORES al reporte (00 y 12 UTC).
HORAS_EXTREMO = 12

# Tiempo presente (ww, tabla 4677 de la OMM) que indica precipitación en la
# estación: 20-27 (en la última hora, no ahora), 50-97 y 99 (ahora).
# No cuentan: 28 (niebla), 29 y 98 (tormenta sin precipitación informada).
WW_PRECIPITACION = set(range(20, 28)) | set(range(50, 98)) | {99}

# Umbrales del equipo de Pronóstico UNLP (km/h), los mismos que usan sus
# importadores (viento_a_categoria / vientoACategoria). Se aplican al viento
# MEDIO horario más alto del día, no a las ráfagas.
CATEGORIAS_VIENTO = (("LEVES", 22), ("MODERADOS", 29), ("REGULARES", 39), ("FUERTES", math.inf))
SECTORES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
CALMA_MS = 0.5

UMBRAL_PP_MM = 0.1


def limites(dia):
    """Día civil argentino -> (inicio, fin) en UTC."""
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=HORA_ARG).astimezone(timezone.utc)
    return inicio, inicio + timedelta(days=1)


def hora_local(ts):
    return ts.astimezone(HORA_ARG).hour


def periodo_de(hora):
    return next(n for n, a, b in PERIODOS if a <= hora < b)


def categoria_viento(kmh):
    return next(c for c, tope in CATEGORIAS_VIENTO if kmh <= tope)


def sector(grados):
    return SECTORES[round(grados / 45) % 8]


def _completitud(tiempos, inicio, fin):
    horas = {(t - inicio) // timedelta(hours=1) for t in tiempos if inicio <= t < fin}
    return round(len(horas) / ((fin - inicio) / timedelta(hours=1)), 2)


# ---------------------------------------------------------------------------
# Temperatura
# ---------------------------------------------------------------------------
def temperaturas(lecturas, extremos, dia):
    """Máxima y mínima del día civil.

    lecturas: [(ts, °C)] de temperatura (horarias del SYNOP o cada pocos
        minutos de una EMA).
    extremos: [(ts, 'tmax'|'tmin', °C)] de los grupos del SYNOP; cada uno
        vale para las 12 h anteriores a ts.

    Las lecturas horarias pueden perderse el extremo real (que ocurre entre
    dos horas), por eso se suman los extremos del SYNOP. Pero el de las
    12 UTC cubre de 21 a 09 hs: arranca 3 horas ANTES del día. Se usa solo si
    es más extremo que todas las lecturas de esas 3 horas de afuera (o sea,
    si seguro ocurrió dentro del día); si no, se descarta.
    Si de esas horas de afuera no hay lecturas (estaciones que no reportan
    de noche), no se puede comprobar: se usa si la mayor parte de su período
    cae dentro del día (el de las 12 UTC del mismo día sí, 9 de 12 horas;
    el de las 12 UTC del día siguiente no, solo 3 de 12), que es la
    convención del SMN.

    Cada extremo vale si el día tiene lecturas casi completas o si lo
    respalda un grupo del SYNOP aceptado ("tmax_ok", "tmin_ok").
    """
    inicio, fin = limites(dia)
    del_dia = [(t, v) for t, v in lecturas if inicio <= t < fin]
    if not del_dia:
        return None
    completitud = _completitud([t for t, _ in del_dia], inicio, fin)
    tmax_t, tmax = max(del_dia, key=lambda x: x[1])
    tmin_t, tmin = min(del_dia, key=lambda x: x[1])
    fuente = {"tmax": "lecturas", "tmin": "lecturas"}
    con_grupo = {"tmax": False, "tmin": False}

    for ts, tipo, valor in extremos:
        desde = ts - timedelta(hours=HORAS_EXTREMO)
        if ts <= inicio or desde >= fin:
            continue
        afuera = [(t, v) for t, v in lecturas if desde <= t < inicio or fin <= t <= ts]
        horas_afuera = (max(inicio - desde, timedelta(0)) + max(ts - fin, timedelta(0))) / timedelta(hours=1)
        if horas_afuera:
            if len({t.replace(minute=0) for t, _ in afuera}) >= 0.6 * horas_afuera:
                # Hay lecturas de la parte de afuera: se comprueba.
                if tipo == "tmin" and not valor < min(v for _, v in afuera) - 0.05:
                    continue
                if tipo == "tmax" and not valor > max(v for _, v in afuera) + 0.05:
                    continue
            elif horas_afuera > HORAS_EXTREMO / 2:
                continue   # sin lecturas para comprobar y casi todo afuera
        con_grupo[tipo] = True
        # La hora: la de la lectura más extrema dentro de la ventana del grupo.
        dentro = [(t, v) for t, v in del_dia if desde <= t <= ts] or del_dia
        if tipo == "tmin" and valor < tmin:
            tmin, fuente["tmin"] = valor, "synop"
            tmin_t = min(dentro, key=lambda x: x[1])[0]
        elif tipo == "tmax" and valor > tmax:
            tmax, fuente["tmax"] = valor, "synop"
            tmax_t = max(dentro, key=lambda x: x[1])[0]

    por_periodo = {}
    for nombre, a, b in PERIODOS:
        vals = [v for t, v in del_dia if a <= hora_local(t) < b]
        if vals:
            por_periodo[nombre] = {"min": min(vals), "max": max(vals), "media": round(sum(vals) / len(vals), 1)}
    return {
        "tmax": tmax, "tmax_hora": hora_local(tmax_t),
        "tmin": tmin, "tmin_hora": hora_local(tmin_t),
        "tmin_periodo": periodo_de(hora_local(tmin_t)),
        "completitud": completitud, "fuente": fuente, "periodos": por_periodo,
        "tmax_ok": completitud >= COMPLETITUD_MINIMA or con_grupo["tmax"],
        "tmin_ok": completitud >= COMPLETITUD_MINIMA or con_grupo["tmin"],
    }


# ---------------------------------------------------------------------------
# Precipitación (¿día con o sin precipitación?)
# ---------------------------------------------------------------------------
def lluvia_ema(lecturas, dia):
    """lecturas: [(ts, mm caídos en el intervalo que termina en ts)]."""
    inicio, fin = limites(dia)
    del_dia = [(t, v) for t, v in lecturas if inicio < t <= fin]
    if not del_dia:
        return None
    mm = round(sum(v for _, v in del_dia), 1)
    completitud = _completitud([t for t, _ in del_dia], inicio, fin)
    por_periodo = defaultdict(float)
    for t, v in del_dia:
        por_periodo[periodo_de(hora_local(t - timedelta(seconds=1)))] += v
    if mm >= UMBRAL_PP_MM:
        pp = True
    elif completitud >= COMPLETITUD_MINIMA:
        pp = False
    else:
        pp = None
    return {"pp": pp, "mm": mm, "completitud": completitud,
            "periodos": {k: round(v, 1) for k, v in por_periodo.items()}}


def lluvia_synop(cantidades, ww, reportes, dia):
    """Precipitación de una estación sinóptica.

    cantidades: [(ts, horas, mm)] de los grupos 6RRRtR / 7RRRR.
    ww: [(ts, código)] de tiempo presente.
    reportes: [ts] de todos los mensajes recibidos (para la completitud).

    Hubo precipitación si (a) algún reporte del día tiene ww de
    precipitación, o (b) alguna cantidad > 0 cuyo período cae al menos en un
    75 % dentro del día. Lo segundo evita que la lluvia de 24 h de las 9 hs
    (que es de 9 a 9) se le asigne entera a un solo día civil.
    No hubo si no hay nada de eso y el día está cubierto: por reportes en
    18 de las 24 horas, o por cantidades en 0 cuyos períodos tapan al menos
    18 horas del día (las estaciones que no reportan de noche igual informan
    la lluvia acumulada de 12 y 24 horas).
    La "traza" (inapreciable) se guarda como 0 mm: cuenta solo si el ww dice
    que hubo llovizna o lluvia.
    """
    inicio, fin = limites(dia)
    completitud = _completitud(reportes, inicio, fin)
    if not completitud and not cantidades:
        return None
    evidencias = []
    for t, codigo in ww:
        if inicio < t <= fin and codigo in WW_PRECIPITACION:
            evidencias.append(f"ww {codigo} a las {hora_local(t):02d} hs")
    horas_secas = set()
    for t, horas, mm in cantidades:
        desde = t - timedelta(hours=horas)
        dentro = (min(t, fin) - max(desde, inicio)) / timedelta(hours=horas)
        if dentro >= 0.75 and mm and mm > 0:
            evidencias.append(f"{mm:g} mm en {horas} h hasta las {hora_local(t):02d} hs")
        if mm == 0:
            # Si no llovió en todo el período, tampoco en la parte que cae en el día.
            h = max(desde, inicio)
            while h < min(t, fin):
                horas_secas.add(h)
                h += timedelta(hours=1)
    if evidencias:
        pp = True
    elif completitud >= COMPLETITUD_MINIMA or len(horas_secas) >= 24 * COMPLETITUD_MINIMA:
        pp = False
    else:
        pp = None
    return {"pp": pp, "completitud": completitud, "evidencias": evidencias[:8]}


# ---------------------------------------------------------------------------
# Viento
# ---------------------------------------------------------------------------
def viento(lecturas, dia):
    """lecturas: [(ts, m/s, grados)]. Se lleva a valores horarios (media de la
    hora; la dirección con promedio circular) para que una EMA que mide cada
    5 minutos se compare igual que el SYNOP, que da un valor por hora."""
    inicio, fin = limites(dia)
    por_hora = defaultdict(list)
    for t, vel, dire in lecturas:
        if inicio <= t < fin and vel is not None:
            por_hora[t.astimezone(HORA_ARG).replace(minute=0, second=0, microsecond=0)].append((vel, dire))
    if not por_hora:
        return None
    horarios = []
    for h, vals in sorted(por_hora.items()):
        vel = sum(v for v, _ in vals) / len(vals)
        con_dir = [(v, d) for v, d in vals if d is not None and v >= CALMA_MS and d != 0]
        dire = None
        if con_dir and vel >= CALMA_MS:
            s = sum(math.sin(math.radians(d)) for _, d in con_dir)
            c = sum(math.cos(math.radians(d)) for _, d in con_dir)
            dire = (math.degrees(math.atan2(s, c)) + 360) % 360
        horarios.append((h, vel, dire))

    conteo = Counter(sector(d) for _, _, d in horarios if d is not None)
    calmas = sum(1 for _, _, d in horarios if d is None)
    vmax_h, vmax, _ = max(horarios, key=lambda x: x[1])
    predominante = None
    if conteo:
        # Desempate: el sector con más viento acumulado.
        fuerza = Counter()
        for _, v, d in horarios:
            if d is not None:
                fuerza[sector(d)] += v
        predominante = max(conteo, key=lambda s: (conteo[s], fuerza[s]))
    kmh = round(vmax * 3.6, 1)
    por_periodo = {}
    for nombre, a, b in PERIODOS:
        hs = [(v, d) for h, v, d in horarios if a <= h.hour < b]
        if hs:
            c = Counter(sector(d) for _, d in hs if d is not None)
            por_periodo[nombre] = {"max_kmh": round(max(v for v, _ in hs) * 3.6, 1),
                                   "sector": c.most_common(1)[0][0] if c else None}
    return {
        "predominante": predominante, "sectores": dict(conteo), "calmas": calmas,
        "max_kmh": kmh, "max_hora": vmax_h.hour, "intensidad": categoria_viento(kmh),
        "media_kmh": round(sum(v for _, v, _ in horarios) / len(horarios) * 3.6, 1),
        "completitud": round(len(horarios) / 24, 2), "periodos": por_periodo,
    }


# ---------------------------------------------------------------------------
# Cálculo para todas las ciudades y guardado
# ---------------------------------------------------------------------------
VARIABLES = ("temp", "tmax", "tmin", "precip", "precip_1h", "precip_3h", "precip_6h",
             "precip_12h", "precip_24h", "ww", "viento_vel", "viento_dir")


def _leer(conn, estaciones, inicio, fin):
    datos = defaultdict(lambda: defaultdict(list))
    with conn.cursor() as cur:
        cur.execute("SELECT id, tipo FROM estacion WHERE id = ANY(%s)", (list(estaciones),))
        tipos = dict(cur.fetchall())
        cur.execute("""SELECT estacion_id, variable, ts, valor FROM observacion
                       WHERE estacion_id = ANY(%s) AND variable = ANY(%s)
                         AND ts >= %s AND ts <= %s AND qc <> 4
                       ORDER BY ts""", (list(estaciones), list(VARIABLES), inicio, fin))
        for est, var, ts, valor in cur:
            datos[est][var].append((ts, valor))
    return tipos, datos


def _armar(tipo, d):
    """Datos crudos de una estación -> argumentos para las funciones de arriba."""
    vel = dict(d["viento_vel"])
    dire = dict(d["viento_dir"])
    reportes = sorted({t for var in VARIABLES for t, _ in d[var]})
    cantidades = [(t, int(var.split("_")[1][:-1]), v)
                  for var in ("precip_1h", "precip_3h", "precip_6h", "precip_12h", "precip_24h")
                  for t, v in d[var]]
    return {
        "sinoptica": tipo == "sinoptica",
        "temp": d["temp"],
        "extremos": [(t, "tmax", v) for t, v in d["tmax"]] + [(t, "tmin", v) for t, v in d["tmin"]],
        "precip": d["precip"], "cantidades": cantidades,
        "ww": [(t, int(v)) for t, v in d["ww"]], "reportes": reportes,
        "viento": [(t, v, dire.get(t)) for t, v in sorted(vel.items())],
    }


def observar_dia(est, dia):
    """Todo lo observado en una estación en un día (o None por elemento)."""
    temp = temperaturas(est["temp"], est["extremos"] if est["sinoptica"] else [], dia)
    if est["sinoptica"]:
        pp = lluvia_synop(est["cantidades"], est["ww"], est["reportes"], dia)
    else:
        pp = lluvia_ema(est["precip"], dia)
    return {"temp": temp, "pp": pp, "viento": viento(est["viento"], dia)}


def filas_ciudad(ciudad, defs, estaciones, dia):
    """Elige, para cada elemento, la primera estación de la lista con el día
    completo. Devuelve filas para verif_observado."""
    filas = []
    cache = {}

    def obs(est_id):
        if est_id not in cache:
            cache[est_id] = observar_dia(estaciones[est_id], dia) if est_id in estaciones else {}
        return cache[est_id]

    for ambito in ("urbana", "suburbana"):
        for est_id in defs.get(ambito) or []:
            t = obs(est_id).get("temp")
            if t and (t["tmax_ok"] or t["tmin_ok"]):
                detalle = {k: t[k] for k in ("completitud", "fuente", "periodos", "tmax_hora", "tmin_hora")}
                if t["tmax_ok"]:
                    filas.append((ciudad, dia, f"tmax_{ambito}", t["tmax"], None, est_id, detalle))
                if t["tmin_ok"]:
                    filas.append((ciudad, dia, f"tmin_{ambito}", t["tmin"], t["tmin_periodo"], est_id, detalle))
                break
    for est_id in defs.get("lluvia") or defs.get("urbana") or []:
        p = obs(est_id).get("pp")
        if p and p["pp"] is not None:
            filas.append((ciudad, dia, "pp", 1.0 if p["pp"] else 0.0, "SI" if p["pp"] else "NO", est_id, p))
            break
    for est_id in defs.get("viento") or defs.get("urbana") or []:
        v = obs(est_id).get("viento")
        if v and v["completitud"] >= COMPLETITUD_VIENTO:
            filas.append((ciudad, dia, "viento_dir", None, v["predominante"], est_id, v))
            filas.append((ciudad, dia, "viento_int", v["max_kmh"], v["intensidad"], est_id, v))
            break
    return filas


# Se procesa de a tramos: leer años de datos de una vez tarda demasiado (cada
# día recorre la lista entera) y deja la conexión abierta sin actividad más
# de lo que la base permite (idle_in_transaction_session_timeout).
DIAS_POR_TRAMO = 31


def calcular(conn, cfg, desde, hasta):
    """Recalcula verif_observado para los días civiles [desde, hasta].
    Solo días ya terminados. Devuelve cuántas filas se guardaron."""
    ayer = (datetime.now(HORA_ARG) - timedelta(days=1)).date()
    hasta = min(hasta, ayer)
    total = 0
    tramo = desde
    while tramo <= hasta:
        fin_tramo = min(tramo + timedelta(days=DIAS_POR_TRAMO - 1), hasta)
        total += _calcular_tramo(conn, cfg, tramo, fin_tramo)
        tramo = fin_tramo + timedelta(days=1)
    log.info("verificación: %d valores observados entre %s y %s", total, desde, hasta)
    return total


def _calcular_tramo(conn, cfg, desde, hasta):
    ciudades = cfg["ciudades"]
    ids = {e for defs in ciudades.values() for lista in defs.values() for e in lista or []}
    inicio = limites(desde)[0] - timedelta(hours=HORAS_EXTREMO)
    fin = limites(hasta)[1] + timedelta(hours=HORAS_EXTREMO)
    tipos, crudos = _leer(conn, ids, inicio, fin)
    conn.commit()   # cierra la transacción de lectura mientras se calcula
    estaciones = {e: _armar(tipos.get(e), crudos[e]) for e in ids if e in crudos}

    filas = []
    dia = desde
    while dia <= hasta:
        for ciudad, defs in ciudades.items():
            filas += filas_ciudad(ciudad, defs, estaciones, dia)
        dia += timedelta(days=1)

    with conn.cursor() as cur:
        # Reemplazo completo del tramo: si una estación dejó de cumplir (ej.
        # se corrigió un dato), no queda una fila vieja.
        cur.execute("DELETE FROM verif_observado WHERE ciudad = ANY(%s) AND fecha BETWEEN %s AND %s",
                    (list(ciudades), desde, hasta))
        cur.executemany(
            """INSERT INTO verif_observado (ciudad, fecha, elemento, valor, categoria, estacion_id, detalle)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(*f[:6], Jsonb(f[6])) for f in filas])
    conn.commit()
    log.info("verificación: %s a %s, %d valores", desde, hasta, len(filas))
    return len(filas)
