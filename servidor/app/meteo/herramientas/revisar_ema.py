"""
Revisión del funcionamiento de una EMA (por ejemplo, una recién instalada).

Se corre en el servidor, con los datos que ya están en la base:

    docker compose exec ingesta python -m meteo.herramientas.revisar_ema ema_verde --dias 8

Opciones:
    --dias N            cuántos días mirar hacia atrás (8 por defecto)
    --desde AAAA-MM-DD  o desde una fecha (por ejemplo, el día de la instalación)
    --refs a,b          estaciones contra las que comparar
                        (por defecto: omm_87593 = La Plata Aero, y bavio = EMA Violeta)

Imprime un informe de texto (nada se modifica en la base):
  1. Cómo envía: cada cuánto, qué porcentaje de las horas tiene datos, los huecos.
  2. Calidad de cada variable: rango, valores trabados, saltos bruscos, humedad
     clavada en 100 %, presión plausible.
  3. Lluvia: total, cómo vienen los valores (¿milímetros por intervalo, pulsos o
     un contador que se acumula?).
  4. Comparación hora a hora con las estaciones de referencia: sesgo (cuánto da
     de más o de menos en promedio), error medio, correlación; y el sesgo de la
     temperatura según la hora del día (si da de más solo al mediodía, el abrigo
     del sensor se calienta con el sol).
  5. Máximas y mínimas diarias contra las de referencia.
"""
import argparse
import math
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from ..ingesta import db

HORA_ARG = timezone(timedelta(hours=-3))
# Saltos sospechosos entre dos lecturas seguidas (separadas hasta 20 minutos).
SALTO = {"temp": 3.0, "td": 3.0, "hum": 20.0, "pres_est": 2.0, "pnm": 2.0}
# Una variable "trabada": el mismo valor durante al menos esto. La presión
# cambia despacio (es normal que quede igual un par de horas): más margen.
TRABADA = {"pres_est": timedelta(hours=8), "pnm": timedelta(hours=8)}
TRABADA_DEFECTO = timedelta(hours=2)
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
COMPARABLES = ("temp", "hum", "pres_est", "pnm", "td")


def leer(conn, estacion, desde, hasta):
    with conn.cursor() as cur:
        cur.execute("""SELECT variable, ts, valor, qc FROM observacion
                       WHERE estacion_id = %s AND ts > %s AND ts <= %s ORDER BY variable, ts""",
                    (estacion, desde, hasta))
        series = defaultdict(list)
        for v, ts, valor, qc in cur.fetchall():
            series[v].append((ts, valor, qc))
    return series


def info_estacion(conn, estacion):
    with conn.cursor() as cur:
        cur.execute("SELECT nombre, tipo, lat, lon FROM estacion WHERE id = %s", (estacion,))
        f = cur.fetchone()
        cur.execute("SELECT max(ts) FROM observacion WHERE estacion_id = %s", (estacion,))
        ultimo = cur.fetchone()[0]
    return (f or (estacion, "?", None, None)), ultimo


def km(a, b):
    if None in (*a, *b):
        return None
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def f(x, d=1):
    return "–" if x is None else f"{x:.{d}f}"


def local(ts):
    return ts.astimezone(HORA_ARG)


def dur(td):
    m = int(td.total_seconds() // 60)
    return f"{m // 1440} d {m % 1440 // 60} h" if m >= 1440 else f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


# ---------------------------------------------------------------------------
def envio(series, desde, hasta, ahora):
    print("\n1. CÓMO ENVÍA")
    todas = sorted({ts for s in series.values() for ts, _, _ in s})
    if not todas:
        print("   ⚠️  No hay ningún dato en el período.")
        return None
    pasos = [(b - a).total_seconds() / 60 for a, b in zip(todas, todas[1:])]
    paso = st.median(pasos) if pasos else None
    horas = {local(t).replace(minute=0, second=0, microsecond=0) for t in todas}
    total_h = int((hasta - max(desde, todas[0])).total_seconds() // 3600) or 1
    print(f"   Primer dato del período: {local(todas[0]):%d/%m %H:%M} · último: {local(todas[-1]):%d/%m %H:%M} "
          f"(hace {dur(ahora - todas[-1])})")
    print(f"   Envía cada {f(paso, 0)} min (mediana) · {len(todas)} envíos · "
          f"horas con datos: {len(horas)} de {total_h} ({100 * len(horas) / total_h:.0f} %)")
    if paso:
        huecos = sorted(((b - a, a, b) for a, b in zip(todas, todas[1:]) if (b - a).total_seconds() / 60 > max(30, 3 * paso)),
                        reverse=True)
        if huecos:
            print(f"   Huecos de más de {max(30, 3 * paso):.0f} min: {len(huecos)} "
                  f"(suman {dur(sum((h[0] for h in huecos), timedelta()))}). Los más largos:")
            for d, a, b in huecos[:8]:
                print(f"     · {dur(d):>12}  del {local(a):%d/%m %H:%M} al {local(b):%d/%m %H:%M}")
        else:
            print("   Sin huecos: envió parejo todo el período. ✓")
    print("   Variables que manda: " + ", ".join(f"{v} ({len(s)})" for v, s in sorted(series.items())))
    return paso


def calidad(series):
    print("\n2. CALIDAD DE CADA VARIABLE")
    for v, s in sorted(series.items()):
        if v == "precip":
            continue
        vals = [x for _, x, q in s if q != 4]
        malos = sum(1 for _, _, q in s if q == 4)
        if not vals:
            print(f"   {v}: todos los datos marcados como malos ({malos}).")
            continue
        linea = f"   {v:9} mín {f(min(vals))} · máx {f(max(vals))} · media {f(st.mean(vals))}"
        if malos:
            linea += f" · ⚠️ {malos} fuera de rango físico (descartados)"
        print(linea)
        # Trabada: el mismo valor muchas lecturas seguidas
        limite = TRABADA.get(v, TRABADA_DEFECTO)
        rachas, ini, prev = [], None, None
        for ts, x, q in s:
            if prev is not None and x == prev[1]:
                ini = ini or prev[0]
            else:
                if ini and prev[0] - ini >= limite:
                    rachas.append((ini, prev[0], prev[1]))
                ini = None
            prev = (ts, x)
        if ini and prev[0] - ini >= limite:
            rachas.append((ini, prev[0], prev[1]))
        for a, b, x in rachas[:4]:
            print(f"       ⚠️ trabada en {f(x)} durante {dur(b - a)} (del {local(a):%d/%m %H:%M} al {local(b):%d/%m %H:%M})")
        if len(rachas) > 4:
            print(f"       … y {len(rachas) - 4} rachas trabadas más")
        # Saltos bruscos
        if v in SALTO:
            saltos = [(b[0], b[1] - a[1]) for a, b in zip(s, s[1:])
                      if (b[0] - a[0]) <= timedelta(minutes=20) and abs(b[1] - a[1]) > SALTO[v]]
            if saltos:
                ej = ", ".join(f"{local(t):%d/%m %H:%M} ({d:+.1f})" for t, d in saltos[:4])
                print(f"       ⚠️ {len(saltos)} saltos de más de {SALTO[v]:g} entre lecturas seguidas: {ej}")
        if v == "hum":
            sat = sum(1 for x in vals if x >= 99.5)
            if sat:
                print(f"       Humedad ≥ 99,5 %: {sat} lecturas ({100 * sat / len(vals):.0f} %). "
                      f"{'⚠️ mucho: puede ser el sensor mojado o saturado' if sat / len(vals) > 0.25 else 'normal en noches húmedas o con niebla'}")
            if max(vals) > 100.5:
                print("       ⚠️ hay humedad por encima de 100 %: el sensor no está bien calibrado")
        if v in ("pres_est", "pnm") and not 950 <= st.mean(vals) <= 1050:
            print(f"       ⚠️ presión media {f(st.mean(vals))} hPa: no parece hPa al nivel de una estación baja "
                  "(¿otra unidad o el campo equivocado?)")


def lluvia(series):
    s = series.get("precip")
    print("\n3. LLUVIA")
    if not s:
        print("   No manda lluvia (o no hubo ninguna lectura en el período).")
        return
    vals = [x for _, x, _ in s]
    no_cero = [x for x in vals if x > 0]
    # Un contador sube y casi nunca baja. Se cuentan solo los CAMBIOS (si no,
    # una serie casi toda en 0 -- 0, 0, 0 -- parece "que nunca baja").
    cambios = [(a, b) for a, b in zip(vals, vals[1:]) if b != a]
    bajadas = sum(1 for a, b in cambios if b < a)
    print(f"   {len(vals)} lecturas · {len(no_cero)} con valor > 0 · suma de todas: {f(sum(vals))} mm")
    if no_cero:
        frec = defaultdict(int)
        for x in no_cero:
            frec[round(x, 2)] += 1
        print("   Valores más comunes (> 0): " + ", ".join(f"{k:g} ({n})" for k, n in sorted(frec.items(), key=lambda kv: -kv[1])[:6]))
    # ¿Las lecturas son múltiplos de un mismo valor? (los vuelcos de la cazoleta)
    if len(no_cero) >= 3:
        paso = min(no_cero)
        if all(abs(x / paso - round(x / paso)) < 0.03 * round(x / paso) for x in no_cero) and len(set(round(x, 2) for x in no_cero)) > 1:
            print(f"   Todas las lecturas > 0 son múltiplos de {paso:g} mm: parece un pluviómetro de cazoleta de {paso:g} mm")
            print("   por vuelco que manda los mm de cada intervalo (varios vuelcos juntos = varias veces ese valor).")
    if len(cambios) > 3 and bajadas <= 0.02 * len(cambios) and len(set(vals)) > 3:
        print("   ⚠️ los valores casi nunca bajan: parece un CONTADOR que se acumula (no mm por intervalo).")
        print("      Si es así, la suma de arriba está mal: hay que usar las diferencias entre lecturas.")
    elif no_cero and len(set(round(x, 2) for x in no_cero)) == 1 and len(no_cero) > 2:
        print(f"   Todas las lecturas > 0 valen lo mismo ({no_cero[0]:g}): parece que manda PULSOS (vuelcos de la cazoleta),")
        print("   como la EMA Violeta (0,68 mm por pulso). Si en estaciones.yaml ya tiene mm_por_pulso, es lo esperado;")
        print("   si no, hay que agregarlo (con la medida de la cazoleta de ESTE pluviómetro) para que sume milímetros.")
    por_dia = defaultdict(float)
    for ts, x, _ in s:
        por_dia[local(ts - timedelta(seconds=1)).date()] += x
    dias_lluvia = {d: round(v, 1) for d, v in sorted(por_dia.items()) if v > 0}
    print("   Por día (suma, hora argentina): " + (", ".join(f"{d:%d/%m}: {v:g}" for d, v in dias_lluvia.items()) or "sin lluvia registrada"))


# ---------------------------------------------------------------------------
def horario(s, tolerancia=timedelta(minutes=10)):
    """{hora en punto: promedio de las lecturas a ±10 min} (o el valor exacto)."""
    por_h = defaultdict(list)
    for ts, x, q in s:
        if q == 4:
            continue
        h = (ts + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
        if abs(ts - h) <= tolerancia:
            por_h[h].append(x)
    return {h: st.mean(v) for h, v in por_h.items()}


def correl(a, b):
    if len(a) < 5 or st.pstdev(a) == 0 or st.pstdev(b) == 0:
        return None
    ma, mb = st.mean(a), st.mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * st.pstdev(a) * st.pstdev(b))


def comparar(series, conn, refs, desde, hasta, nombre_ref, tramo_dia):
    print("\n4. COMPARACIÓN HORA A HORA (la EMA menos la referencia)")
    for ref in refs:
        rs = leer(conn, ref, desde, hasta)
        if not rs:
            print(f"   {nombre_ref[ref]}: sin datos en el período.")
            continue
        print(f"   Contra {nombre_ref[ref]}:")
        algo = False
        for v in COMPARABLES:
            if v not in series or v not in rs:
                continue
            a, b = horario(series[v]), horario(rs[v])
            comunes = sorted(set(a) & set(b))
            if len(comunes) < 3:
                continue
            algo = True
            d = [a[h] - b[h] for h in comunes]
            r = correl([a[h] for h in comunes], [b[h] for h in comunes])
            print(f"     {v:9} {len(comunes):4} horas · sesgo {f(st.mean(d)):>5} · error medio {f(st.mean(map(abs, d))):>4}"
                  f" · máx. diferencia {f(max(d, key=abs)):>5} · correlación {f(r, 2)}")
            if v == "temp" and tramo_dia:
                tramos = defaultdict(list)
                for h, x in zip(comunes, d):
                    hl = local(h).hour
                    tramos["madrugada (0-6)" if hl < 6 else "mañana (6-12)" if hl < 12 else "tarde (12-18)" if hl < 18 else "noche (18-24)"].append(x)
                print("               sesgo por hora del día: " + " · ".join(
                    f"{k} {f(st.mean(tramos[k])):>5}" for k in ("madrugada (0-6)", "mañana (6-12)", "tarde (12-18)", "noche (18-24)") if tramos[k]))
        if not algo:
            print("     (no hay variables en común con horas coincidentes)")
        extremos(series, rs, nombre_ref[ref])


def extremos(series, rs, nombre):
    if "temp" not in series or "temp" not in rs:
        return
    def por_dia(s):
        d = defaultdict(list)
        for ts, x, q in s:
            if q != 4:
                d[local(ts).date()].append(x)
        return {k: (min(v), max(v), len(v)) for k, v in d.items()}
    a, b = por_dia(series["temp"]), por_dia(rs["temp"])
    dias = sorted(set(a) & set(b))
    if not dias:
        return
    print(f"     Extremos diarios (EMA / {nombre}):")
    for k in dias:
        print(f"       {DIAS[k.weekday()]} {k:%d/%m}: mín {f(a[k][0])} / {f(b[k][0])} ({a[k][0] - b[k][0]:+.1f}) · "
              f"máx {f(a[k][1])} / {f(b[k][1])} ({a[k][1] - b[k][1]:+.1f})")


def main(argv=None):
    p = argparse.ArgumentParser(description="Revisa el funcionamiento de una EMA con los datos de la base.")
    p.add_argument("estacion")
    p.add_argument("--dias", type=float, default=8)
    p.add_argument("--desde", type=date.fromisoformat)
    p.add_argument("--refs", default="omm_87593,bavio")
    a = p.parse_args(argv)
    ahora = datetime.now(timezone.utc)
    desde = (datetime.combine(a.desde, datetime.min.time(), HORA_ARG) if a.desde else ahora - timedelta(days=a.dias))
    conn = db.conectar()
    (nombre, tipo, lat, lon), ultimo = info_estacion(conn, a.estacion)
    print(f"REVISIÓN DE {nombre} ({a.estacion}) · del {local(desde):%d/%m %H:%M} al {local(ahora):%d/%m %H:%M} (hora argentina)")
    refs = [r for r in a.refs.split(",") if r and r != a.estacion]
    nombre_ref = {}
    for r in refs:
        (n, _, rla, rlo), ult = info_estacion(conn, r)
        dist = km((lat, lon), (rla, rlo))
        nombre_ref[r] = n
        print(f"   Referencia: {n} ({r}) · a {f(dist, 1)} km según las coordenadas cargadas"
              f" · su último dato: {local(ult):%d/%m %H:%M}" if ult else f"   Referencia: {n} ({r}) · sin datos")
    if lat is not None:
        print(f"   Coordenadas cargadas de la EMA: {lat:.4f}, {lon:.4f}  "
              "(si la mudaron, hay que corregirlas en config/estaciones.yaml)")
    series = leer(conn, a.estacion, desde, ahora)
    envio(series, desde, ahora, ahora)
    if series:
        calidad(series)
        lluvia(series)
        comparar(series, conn, refs, desde, ahora, nombre_ref, tramo_dia=True)
    print("\nCómo leerlo: sesgo = cuánto da de más (+) o de menos (−) en promedio; error medio = la diferencia típica.\n"
          "Entre estaciones a 20-30 km, diferencias de 1-2 °C son normales (sobre todo de noche, con calma).\n"
          "Un sesgo positivo solo a la tarde suele ser el abrigo del sensor calentado por el sol.")
    conn.close()


if __name__ == "__main__":
    main()
