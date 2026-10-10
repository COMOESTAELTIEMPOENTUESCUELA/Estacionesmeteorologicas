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


def precip_por_intervalo(acumulados):
    """[(ts, acumulado_del_día)] ordenados -> [(ts, lluvia_del_intervalo, qc)].

    Si el acumulado baja, la estación se reinició: el valor nuevo es lo que
    llovió desde el reinicio. El reinicio normal es a medianoche. Si en cambio
    el acumulado SUBE y en el dato siguiente VUELVE A BAJAR fuera de la
    medianoche (visto en Los Talas: 0 -> 0,25 -> 0), no se puede saber si fue
    un vuelco real con la consola mal configurada o un pulso falso: esa lluvia
    se guarda igual, pero marcada como sospechosa (qc = 3).
    """
    salida = []
    anterior = None
    for ts, acum in acumulados:
        if acum is None:
            continue
        local = ts.astimezone(HORA_ARG)
        cerca_medianoche = local.hour == 0 and local.minute < 20
        if anterior is None:
            delta = None  # primer dato del día: no sabemos desde cuándo acumula
            if cerca_medianoche:
                delta = acum  # justo después de medianoche: es todo del intervalo
        elif acum >= anterior:
            delta = acum - anterior
        else:
            delta = acum
            if not cerca_medianoche and salida and salida[-1][1] > 0:
                # Reinicio fuera de hora justo después de registrar lluvia.
                t_ant, v_ant, _ = salida[-1]
                salida[-1] = (t_ant, v_ant, 3)
        if delta is not None:
            salida.append((ts, round(delta, 2), 0))
        anterior = acum
    return salida


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
