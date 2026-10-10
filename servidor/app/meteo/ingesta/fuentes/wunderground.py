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
from datetime import datetime, timedelta

from ..base import HORA_ARG, KMH_A_MS, Obs, num

URL = "https://api.weather.com/v2/pws/history/all"


def precip_por_intervalo(acumulados, lecturas_para_confirmar=3):
    """[(ts, acumulado_del_día)] ordenados -> [(ts, lluvia_del_intervalo, qc)].

    Técnica del MÁXIMO ACUMULADO: solo cuenta como lluvia nueva lo que supera
    el máximo acumulado visto hasta ese momento del día. Así el total del día
    es el mayor acumulado que informó la estación, que es lo que muestra
    Wunderground en su propio resumen diario.

    Fallas reales del contador que esto resuelve (Los Talas y ES7, oct/2026):
    - Bajadas de un vuelco: 9,40 -> 8,89 y se queda ahí dos horas. Es ruido
      del contador, no un reinicio: se ignora.
    - Un "0" suelto en medio de la tormenta (20 -> 0 -> 20,5): se ignora.
    - El acumulado sube y vuelve a 0 enseguida (0 -> 0,25 -> 0): no se sabe si
      fue un vuelco real o un pulso falso: queda marcado como sospechoso.

    Reinicio real = el acumulado cae a casi 0 (<= 0,3 mm o < 5 % del máximo)
    y se queda abajo `lecturas_para_confirmar` lecturas, o pasa a medianoche.
    Si hubo una caída a casi 0 fuera de la medianoche, el contador se comportó
    raro ese día: toda su lluvia queda con qc = 3 (se guarda igual).
    """
    datos = [(ts, a) for ts, a in acumulados if a is not None]
    salida = []
    maximo = None
    inestable = False
    for i, (ts, acum) in enumerate(datos):
        local = ts.astimezone(HORA_ARG)
        cerca_medianoche = local.hour == 0 and local.minute < 20
        if maximo is None:
            # Primer dato del día: es la línea de base, no se sabe desde cuándo
            # acumula. (Se puede perder la lluvia de los primeros minutos.) Se
            # guarda como 0 para que, al reprocesar, pise cualquier valor viejo.
            maximo = acum
            salida.append((ts, 0.0))
            continue
        if acum < maximo - 1e-9:
            casi_cero = acum <= max(0.3, 0.05 * maximo)
            if casi_cero and not cerca_medianoche:
                inestable = True
            siguientes = [a for _, a in datos[i + 1:i + 1 + lecturas_para_confirmar]]
            reinicio = casi_cero and (cerca_medianoche or all(a < maximo - 1e-9 for a in siguientes))
            if reinicio:
                maximo = acum
                salida.append((ts, round(acum, 2)))   # lo que llovió desde el reinicio
            else:
                salida.append((ts, 0.0))              # ruido del contador: se ignora
            continue
        salida.append((ts, round(acum - maximo, 2)))
        maximo = acum
    qc = 3 if inestable else 0
    return [(ts, v, qc if v > 0 else 0) for ts, v in salida]


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


def traer(estacion, desde, hasta, sesion):
    cfg = estacion["config"]
    obs = []
    dia = desde.astimezone(HORA_ARG).date()
    ultimo = hasta.astimezone(HORA_ARG).date()
    while dia <= ultimo:
        r = sesion.get(URL, params={
            "stationId": cfg["wu_id"], "date": dia.strftime("%Y%m%d"),
            "format": "json", "units": "m", "numericPrecision": "decimal",
            "apiKey": cfg["wu_key"],
        }, timeout=60)
        if r.status_code == 204:  # sin datos ese día
            dia += timedelta(days=1)
            continue
        r.raise_for_status()
        obs += parsear_dia(r.json(), estacion["id"])
        dia += timedelta(days=1)
    return obs
