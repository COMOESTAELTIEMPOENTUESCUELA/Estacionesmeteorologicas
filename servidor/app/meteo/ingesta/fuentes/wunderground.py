"""
Weather Underground (API de The Weather Company para estaciones personales).

    https://api.weather.com/v2/pws/history/all?stationId=ID&date=AAAAMMDD&format=json&units=m&apiKey=KEY

Devuelve todas las observaciones de un día (hora local de la estación),
normalmente cada 5 minutos. Con units=m:
  - temperatura en °C, presión en hPa (REDUCIDA AL NIVEL DEL MAR), viento en km/h
  - precipTotal: lluvia ACUMULADA desde la medianoche local (se reinicia cada día)

Por eso la lluvia de cada intervalo se calcula como la diferencia entre
acumulados consecutivos. Por esa misma razón conviene pedir siempre días
completos.
"""
import time
from datetime import datetime, timedelta

from ..base import HORA_ARG, KMH_A_MS, Obs, num

URL = "https://api.weather.com/v2/pws/history/all"
# "Últimas 24 h": casi en tiempo real. El historial del día EN CURSO puede ir
# horas atrasado (Wunderground lo arma de su archivo procesado), así que para
# hoy se combinan las dos consultas.
URL_24H = "https://api.weather.com/v2/pws/observations/all/1day"


def precip_por_intervalo(acumulados, lecturas_para_confirmar=3):
    """[(ts, acumulado_del_día)] ordenados -> [(ts, lluvia_del_intervalo, qc)].

    Técnica del ÚLTIMO VALOR (el «mínimo hacia adelante»): un pluviómetro de
    vuelcos no puede des-llover, así que si el acumulado sube y después baja,
    la subida no era real. El acumulado verdadero en cada momento es el menor
    de los que informa la estación desde ese momento hasta el final del tramo:

        informado:   3,05  3,30  3,05  ...  7,62  9,91  8,89
        verdadero:   3,05  3,05  3,05  ...  7,62  8,89  8,89

    y la lluvia de cada lectura es la diferencia entre verdaderos seguidos. El
    total del día es el ÚLTIMO acumulado que informó la estación (lo que muestra
    la tarjeta de Wunderground), no el máximo. Mientras el día está en curso,
    una subida falsa se cuenta hasta que la estación la corrige; como cada
    consulta recalcula el día entero, al bajar se descuenta sola.

    Fallas reales del contador (Los Talas y ES7, oct-nov/2026):
    - Subidas que se deshacen: 9,91 -> 8,89 y se queda ahí: no se cuentan.
    - El acumulado sube un vuelco y vuelve a 0 (0 -> 0,25 -> 0): no se cuenta.
    - Un "0" suelto en medio de la tormenta (20 -> 0 -> 20,5): se ignora la
      lectura (si no, el «mínimo hacia adelante» borraría todo lo anterior).

    Reinicio real = el acumulado cae a casi 0 (<= 0,3 mm o < 5 % del máximo) y
    se queda abajo `lecturas_para_confirmar` lecturas habiendo acumulado más de
    un vuelco antes, o pasa a medianoche. Cada tramo entre reinicios se cuenta
    por separado. Si hubo un reinicio o un 0 suelto fuera de la medianoche, el
    contador se comportó raro ese día: su lluvia queda con qc = 3 (se guarda).
    """
    datos = [(ts, a) for ts, a in acumulados if a is not None]
    # 1) Partir en tramos entre reinicios y descartar los ceros sueltos.
    tramos, ignoradas = [[]], set()
    maximo = None
    inestable = False
    for i, (ts, acum) in enumerate(datos):
        local = ts.astimezone(HORA_ARG)
        cerca_medianoche = local.hour == 0 and local.minute < 20
        if maximo is not None and acum < maximo - 1e-9 and acum <= max(0.3, 0.05 * maximo):
            siguientes = [a for _, a in datos[i + 1:i + 1 + lecturas_para_confirmar]]
            if cerca_medianoche:
                tramos.append([])
                maximo = None
            elif maximo > 0.3 and all(a < maximo - 1e-9 for a in siguientes):
                tramos.append([])                 # la consola se reinició
                maximo = None
                inestable = True
            elif maximo > 0.3:
                ignoradas.add(i)                  # cero suelto: se ignora
                inestable = True
                continue
            # (si antes había un solo vuelco, la bajada se resuelve abajo)
        tramos[-1].append(i)
        maximo = acum if maximo is None else max(maximo, acum)

    # 2) En cada tramo, el verdadero es el mínimo desde ahí hasta el final.
    lluvia = {i: 0.0 for i in range(len(datos))}
    for n, tramo in enumerate(t for t in tramos if t):
        verdadero, minimo = {}, None
        for i in reversed(tramo):
            minimo = datos[i][1] if minimo is None else min(minimo, datos[i][1])
            verdadero[i] = minimo
        # El primer tramo del día arranca de su línea de base (no se sabe desde
        # cuándo acumula); uno que empieza en un reinicio arranca de 0.
        anterior = verdadero[tramo[0]] if n == 0 else 0.0
        for i in tramo:
            lluvia[i] = round(max(0.0, verdadero[i] - anterior), 2)
            anterior = verdadero[i]
    qc = 3 if inestable else 0
    return [(ts, lluvia[i], qc if lluvia[i] > 0 else 0) for i, (ts, _) in enumerate(datos)]


def parsear_dia(datos, estacion_id):
    observaciones = sorted((datos or {}).get("observations") or [], key=lambda o: o["obsTimeUtc"])
    obs = []
    acumulados = []
    for o in observaciones:
        ts = datetime.fromisoformat(o["obsTimeUtc"].replace("Z", "+00:00"))
        m = o.get("metric") or {}

        def agregar(variable, valor, factor=1.0):
            v = num(valor)
            if v is not None:
                obs.append(Obs(ts, estacion_id, variable, round(v * factor, 3)))

        agregar("temp", m.get("tempAvg"))
        agregar("td", m.get("dewptAvg"))
        agregar("hum", o.get("humidityAvg"))
        pmax, pmin = num(m.get("pressureMax")), num(m.get("pressureMin"))
        if pmax is not None and pmin is not None:
            obs.append(Obs(ts, estacion_id, "pnm", round((pmax + pmin) / 2, 2)))
        agregar("viento_vel", m.get("windspeedAvg"), KMH_A_MS)
        agregar("racha", m.get("windgustHigh"), KMH_A_MS)
        agregar("viento_dir", o.get("winddirAvg"))
        agregar("rad_solar", o.get("solarRadiationHigh"))
        agregar("intens_precip", m.get("precipRate"))
        acumulados.append((ts, num(m.get("precipTotal"))))

    for ts, p, qc in precip_por_intervalo(acumulados):
        obs.append(Obs(ts, estacion_id, "precip", p, qc))
    return obs


def dias_a_reemplazar(estacion_id, obs):
    """La lluvia de cada día se calcula con el día completo: al guardar, se
    reemplaza toda la lluvia de los días traídos (hora argentina, 00 a 24)."""
    dias = {o.ts.astimezone(HORA_ARG).date() for o in obs if o.variable == "precip"}
    rangos = []
    for d in sorted(dias):
        inicio = datetime(d.year, d.month, d.day, tzinfo=HORA_ARG)
        rangos.append((estacion_id, "precip", inicio, inicio + timedelta(days=1) - timedelta(microseconds=1)))
    return rangos


def _pedir(sesion, url, params, intentos=3):
    """GET con reintentos: Wunderground a veces corta la conexión sin responder."""
    for i in range(intentos):
        try:
            r = sesion.get(url, params=params, timeout=60)
            if r.status_code == 204:  # sin datos
                return []
            r.raise_for_status()
            return (r.json() or {}).get("observations") or []
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(3 * (i + 1))


def traer(estacion, desde, hasta, sesion):
    cfg = estacion["config"]
    base = {"stationId": cfg["wu_id"], "format": "json", "units": "m",
            "numericPrecision": "decimal", "apiKey": cfg["wu_key"]}
    hoy = datetime.now(HORA_ARG).date()
    dia = desde.astimezone(HORA_ARG).date()
    ultimo = hasta.astimezone(HORA_ARG).date()

    crudas = []
    while dia <= ultimo:
        crudas += _pedir(sesion, URL, {**base, "date": dia.strftime("%Y%m%d")})
        dia += timedelta(days=1)
    if ultimo >= hoy:
        crudas += _pedir(sesion, URL_24H, base)

    # Juntar sin repetidos (la misma lectura puede venir en las dos consultas)
    # y separar por día local: la lluvia de cada día se calcula con el día entero.
    por_ts = {o["obsTimeUtc"]: o for o in crudas if o.get("obsTimeUtc")}
    por_dia = {}
    for o in por_ts.values():
        ts = datetime.fromisoformat(o["obsTimeUtc"].replace("Z", "+00:00"))
        por_dia.setdefault(ts.astimezone(HORA_ARG).date(), []).append(o)
    desde_dia = desde.astimezone(HORA_ARG).date()
    obs = []
    for d in sorted(por_dia):
        if desde_dia <= d <= ultimo:
            obs += parsear_dia({"observations": por_dia[d]}, estacion["id"])
    return obs
