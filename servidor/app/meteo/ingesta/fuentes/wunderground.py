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
    el máximo acumulado visto hasta ese momento del día.

    Por qué: algunas consolas (visto en Los Talas y ES7, 08/10/2026) mandan un
    "0" suelto en medio de una tormenta y en la lectura siguiente vuelven al
    acumulado real (20 -> 0 -> 20,5). Restando lecturas consecutivas, ese 0
    hacía contar de nuevo los 20 mm: daba 259 mm donde las vecinas midieron 23.
    Con el máximo acumulado, el total del día nunca supera el mayor acumulado
    que informó la estación.

    - Una bajada que se mantiene `lecturas_para_confirmar` lecturas seguidas (o
      que pasa cerca de la medianoche) es un reinicio real: se empieza a contar
      de nuevo desde ahí.
    - Una bajada que no se mantiene es una falla del contador: se ignora.
    - Si en el día hubo alguna bajada fuera de la medianoche, el contador fue
      inestable: TODA la lluvia de ese día queda marcada como sospechosa
      (qc = 3). Se guarda igual; el análisis decide si la usa.
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
            siguientes = [a for _, a in datos[i + 1:i + 1 + lecturas_para_confirmar]]
            reinicio = cerca_medianoche or all(a < maximo - 1e-9 for a in siguientes)
            if not cerca_medianoche:
                inestable = True
            if reinicio:
                maximo = acum
                salida.append((ts, round(acum, 2)))   # lo que llovió desde el reinicio
            else:
                salida.append((ts, 0.0))              # falla pasajera: se ignora
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
