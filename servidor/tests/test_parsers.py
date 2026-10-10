"""Tests de los recolectores con datos de ejemplo (no usan internet)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meteo.ingesta import synop
from meteo.ingesta.base import HORA_ARG
from meteo.ingesta.fuentes import comunidad, davis, pronosticos, thingspeak, wunderground

DATOS = Path(__file__).parent / "datos"
T = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def como_dict(obs):
    return {o.variable: o.valor for o in obs}


# --------------------------------------------------------------- SYNOP
def test_synop_completo():
    msg = ("AAXX 10121 87593 32970 72106 10142 20118 30138 40220 52010 60001 78082 83530 "
           "333 10187 20089 56999 70125 82630=")
    d = como_dict(synop.decodificar(msg, T, "omm_87593"))
    assert d["visibilidad"] == 20000      # VV=70 -> 20 km
    assert d["nubosidad"] == 7
    assert d["viento_dir"] == 210
    assert d["viento_vel"] == 6           # i_w=1: m/s
    assert d["temp"] == 14.2
    assert d["td"] == 11.8
    assert d["pres_est"] == 1013.8
    assert d["pnm"] == 1022.0
    assert d["precip_6h"] == 0.0          # 6 000 1
    assert d["ww"] == 80
    assert d["tmax"] == 18.7              # sección 333
    assert d["tmin"] == 8.9
    assert d["precip_24h"] == 12.5        # 7 0125 -> 12,5 mm


def test_synop_negativos_nudos_y_traza():
    msg = "AAXX 10064 87593 11458 80510 11025 21031 39985 49990 69902 333 79999="
    d = como_dict(synop.decodificar(msg, T, "x"))
    assert d["temp"] == -2.5
    assert d["td"] == -3.1
    assert d["pres_est"] == 998.5
    assert d["pnm"] == 999.0
    assert d["viento_vel"] == pytest.approx(10 * 0.514444, abs=1e-3)  # i_w=4: nudos
    assert d["precip_12h"] == 0.0         # 990 = traza
    assert d["precip_24h"] == 0.0         # 9999 = traza


def test_synop_seccion_nacional_no_se_confunde():
    msg = "AAXX 10121 87593 32970 72106 10142 555 10350="
    d = como_dict(synop.decodificar(msg, T, "x"))
    assert "tmax" not in d and d["temp"] == 14.2


def test_synop_nil():
    assert synop.decodificar("AAXX 10121 87593 NIL=", T, "x") == []


def test_parsear_getsynop():
    texto = "87593,2026,10,10,12,00,AAXX 10121 87593 32970=\nbasura\n"
    [(ind, ts, msg)] = synop.parsear_getsynop(texto)
    assert ind == "87593" and ts == T and msg.startswith("AAXX")


# --------------------------------------------------------------- Davis
def test_davis_archivo_real():
    obs = davis.parsear((DATOS / "davis_muestra.txt").read_text(), "observatorio_davis")
    primera = [o for o in obs if o.ts == datetime(2026, 10, 3, 0, 5, tzinfo=HORA_ARG)]
    d = como_dict(primera)
    assert d["temp"] == 14.6 and d["hum"] == 70 and d["td"] == 9.1
    assert d["pnm"] == 1010.6 and d["precip"] == 0.0
    assert "viento_dir" not in d          # '---' = calma / sin dato
    # La hora local 00:05 es 03:05 UTC.
    assert primera[0].ts.astimezone(timezone.utc).hour == 3


# --------------------------------------------------------------- ThingSpeak
def test_thingspeak_campos_y_factor():
    feeds = [{"created_at": "2026-10-10T12:00:00Z", "field1": "15.5", "field2": "80",
              "field5": "2", "field6": None}]
    campos = {"field1": "temp", "field2": "hum", "field5": {"variable": "precip", "mm_por_pulso": 0.68},
              "field7": {"variable": "viento_vel", "factor": 0.277778}}
    feeds[0]["field7"] = "36"
    d = como_dict(thingspeak.parsear_feeds(feeds, "bavio", campos))
    # field5 = "2" es UN vuelco de 0,68 mm (no 2 x 0,68), como en red-meteorologica.html
    assert d == {"temp": 15.5, "hum": 80.0, "precip": 0.68, "viento_vel": 10.0}
    feeds[0]["field5"] = "0"
    assert como_dict(thingspeak.parsear_feeds(feeds, "bavio", campos))["precip"] == 0.0


# --------------------------------------------------------------- Wunderground
def _serie(inicio_utc, valores, paso_min=5):
    return [(inicio_utc + timedelta(minutes=paso_min * i), v) for i, v in enumerate(valores)]


def test_wunderground_lluvia_normal():
    t0 = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)  # 12 hora argentina
    res = wunderground.precip_por_intervalo(_serie(t0, [1.0, 1.0, 2.2, 2.2, 4.0, 4.5]))
    assert [v for _, v, _ in res] == [0.0, 0.0, 1.2, 0.0, 1.8, 0.5]
    assert all(q == 0 for _, _, q in res)


def test_wunderground_cero_suelto_en_medio_de_la_tormenta_no_duplica():
    # Patrón que daba 259 mm en Los Talas el 08/10: un 0 suelto y vuelta al acumulado.
    t0 = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    res = wunderground.precip_por_intervalo(_serie(t0, [5.0, 10.0, 20.0, 0.0, 20.5, 23.0]))
    assert round(sum(v for _, v, _ in res), 2) == 18.0      # = 23 - 5 (la base del día)
    assert all(q == 3 for _, v, q in res if v > 0)        # día con contador inestable


def test_wunderground_reinicio_real_fuera_de_hora():
    # La consola se reinicia a las 12 y sigue acumulando desde 0: se cuenta.
    t0 = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
    res = wunderground.precip_por_intervalo(_serie(t0, [0.0, 3.0, 0.0, 0.0, 0.5, 1.0, 1.0]))
    assert round(sum(v for _, v, _ in res), 2) == 4.0       # 3 antes + 1 después del reinicio


def test_wunderground_pulsos_que_vuelven_a_cero_quedan_sospechosos():
    # Patrón real de Los Talas (10/10/2026): el acumulado sube a 0,25 y vuelve a 0.
    t0 = datetime(2026, 10, 10, 8, 40, tzinfo=timezone.utc)  # 05:40 hora argentina
    res = wunderground.precip_por_intervalo(_serie(t0, [0.0, 0.25, 0.0, 0.0, 0.25, 0.0]))
    assert [(v, q) for _, v, q in res if v > 0] == [(0.25, 3)]


def test_wunderground_cada_lectura_genera_una_fila():
    # Al reprocesar, cada horario tiene que tener fila para pisar valores viejos.
    t0 = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
    lecturas = _serie(t0, [1.0, 0.0, 1.5, 2.0])
    assert [ts for ts, _, _ in wunderground.precip_por_intervalo(lecturas)] == [ts for ts, _ in lecturas]


def test_wunderground_reinicio_de_medianoche_es_normal():
    ayer = datetime(2026, 10, 10, 2, 50, tzinfo=timezone.utc)   # 23:50 hora argentina
    res = wunderground.precip_por_intervalo(_serie(ayer, [3.0, 3.0, 0.0, 0.3], paso_min=10))
    assert [(v, q) for _, v, q in res] == [(0.0, 0), (0.0, 0), (0.0, 0), (0.3, 0)]


def test_wunderground_parsear_dia():
    datos = {"observations": [{
        "obsTimeUtc": "2026-10-10T15:00:00Z", "humidityAvg": 70, "winddirAvg": 180,
        "solarRadiationHigh": 500,
        "metric": {"tempAvg": 20.0, "dewptAvg": 14.0, "windspeedAvg": 36.0, "windgustHigh": 54.0,
                   "pressureMax": 1015.0, "pressureMin": 1014.0, "precipRate": 0, "precipTotal": 0},
    }]}
    d = como_dict(wunderground.parsear_dia(datos, "es7_ep20"))
    assert d["viento_vel"] == 10.0 and d["racha"] == 15.0   # km/h -> m/s
    assert d["pnm"] == 1014.5 and d["temp"] == 20.0


# --------------------------------------------------------------- Comunidad
def test_comunidad_csv_columnas_por_nombre():
    csv = ('"Fecha","Tipo","Escuela","Reportero","Lluvia_mm","Estado_Camino","Resumen",'
           '"Imagen_URL","Lat","Lon","Instrumento","Periodo"\n'
           '"10/10/2026 09:05:00","lluvia","EP 23 Bavio","Ana","12,5","","Llovió fuerte","","-35","-57.7",'
           '"Pluviómetro convencional","24 h hasta las 9"\n'
           '"","","","","","","","","","","",""\n')
    [r] = comunidad.parsear_csv(csv, {"EP 23 Bavio": "bavio"})
    assert r["lluvia_mm"] == 12.5
    assert r["instrumento"] == "pluviometro_convencional"
    assert r["periodo"] == "24h_9hs"
    assert r["estacion_id"] == "bavio"
    assert r["ts"] == datetime(2026, 10, 10, 9, 5, tzinfo=HORA_ARG)


def test_comunidad_planilla_vieja_sin_columnas_nuevas():
    csv = ('"Fecha","Tipo","Escuela","Reportero","Lluvia_mm","Estado_Camino","Resumen","Imagen_URL","Lat","Lon"\n'
           '"10/10/2026","rocio","EP 21","Juan","","","Rocío en el pasto","","",""\n')
    [r] = comunidad.parsear_csv(csv)
    assert r["instrumento"] == "sin_dato" and r["lluvia_mm"] is None


# --------------------------------------------------------------- Pronósticos
def test_pronostico_real_desarmado():
    p = json.loads((DATOS / "pronostico_laplata.json").read_text())
    filas = pronosticos.desarmar(p)
    assert filas[0]["plazo_dias"] == 0
    assert [f["plazo_dias"] for f in filas] == list(range(len(filas)))
    assert filas[0]["temp_max"] is not None
    assert pronosticos.huella(p) == pronosticos.huella(dict(p))


def test_davis_usa_respaldo_si_la_facultad_no_responde():
    class Resp:
        def __init__(self, texto):
            self.text = texto

        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, **kw):
            if "fcaglp" in url:
                raise ConnectionError("timeout")
            return Resp("respaldo")

    cfg = {"url": "https://meteo.fcaglp.unlp.edu.ar/x.txt", "url_respaldo": "https://raw.githubusercontent.com/x.txt",
           "verificar_ssl": False}
    assert davis.bajar(cfg, Sesion()) == "respaldo"


def test_comunidad_fecha_formato_eeuu():
    assert comunidad.parsear_fecha("6/18/2026 14:05:00") == datetime(2026, 6, 18, 14, 5, tzinfo=HORA_ARG)
    assert comunidad.parsear_fecha("18/6/2026 14:05:00") == datetime(2026, 6, 18, 14, 5, tzinfo=HORA_ARG)


def test_synop_real_la_plata_12utc():
    # Mensaje real de La Plata Aero, 10/10/2026 12 UTC (llovizna, 3 mm en 24 h).
    msg = ("AAXX 10124 87593 01359 81809 10118 20116 30212 40239 51007 60034 75065 886// "
           "333 10125 20098 32010 56499 60021 88708=")
    d = como_dict(synop.decodificar(msg, T, "omm_87593"))
    assert d["precip_24h"] == 3.0   # sección 1: 6 003 4
    assert d["precip_6h"] == 2.0    # sección 3: 6 002 1
    assert d["visibilidad"] == 9000 and d["nubosidad"] == 8 and d["viento_dir"] == 180
    assert d["viento_vel"] == pytest.approx(9 * 0.514444, abs=1e-3)  # i_w = 4: nudos
    assert (d["temp"], d["td"], d["pres_est"], d["pnm"]) == (11.8, 11.6, 1021.2, 1023.9)
    assert (d["tmax"], d["tmin"], d["ww"]) == (12.5, 9.8, 50)
