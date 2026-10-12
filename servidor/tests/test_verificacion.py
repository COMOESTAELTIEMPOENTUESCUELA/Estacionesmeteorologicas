"""Verificación de pronósticos: definiciones de lo observado, lo pronosticado
y las medidas de error. Sin base de datos."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from meteo.ingesta.fuentes import pronosticos
from meteo.verificacion import observado as ob
from meteo.verificacion import pronostico, puntajes

AR = timezone(timedelta(hours=-3))
DIA = date(2026, 10, 13)


def local(h, dia=DIA, m=0):
    return datetime(dia.year, dia.month, dia.day, tzinfo=AR) + timedelta(hours=h, minutes=m)


def horarias(valores, dia=DIA):
    """{hora local: valor} -> [(ts, valor)]"""
    return [(local(h, dia), v) for h, v in valores.items()]


# ---------------------------------------------------------------------------
# Temperatura
# ---------------------------------------------------------------------------
def test_minima_nocturna_despues_de_un_frente():
    # Mañana templada (mín 14 a las 6), frente a la tarde, y a las 23 hace 9.
    t = {h: 14 + h * 0.5 for h in range(0, 16)}
    t[6] = 14.0
    t.update({16: 20, 17: 18, 18: 16, 19: 14, 20: 12, 21: 11, 22: 10, 23: 9})
    r = ob.temperaturas(horarias(t), [], DIA)
    assert (r["tmin"], r["tmin_hora"], r["tmin_periodo"]) == (9, 23, "noche")
    assert r["periodos"]["madrugada"]["min"] == 14.0   # queda guardada para analizar
    assert r["completitud"] == 1.0


def test_extremo_synop_de_las_12utc_se_usa_si_ocurrio_dentro_del_dia():
    ayer = DIA - timedelta(days=1)
    lecturas = horarias({21: 12.0, 22: 11.6, 23: 11.2}, ayer) + horarias({h: 9.0 + abs(h - 6) * 0.4 for h in range(24)})
    tmin_12utc = (datetime(2026, 10, 13, 12, tzinfo=timezone.utc), "tmin", 8.3)   # 21 a 09 hs
    r = ob.temperaturas(lecturas, [tmin_12utc], DIA)
    assert r["tmin"] == 8.3 and r["fuente"]["tmin"] == "synop" and r["tmin_hora"] == 6


def test_extremo_synop_se_descarta_si_puede_ser_de_la_noche_anterior():
    # La noche anterior ya hacía 5 grados (a las 23): la mínima de 5,0 que
    # informa el SYNOP de las 12 UTC puede ser de ESA noche, no de hoy.
    ayer = DIA - timedelta(days=1)
    lecturas = horarias({21: 6.0, 22: 5.4, 23: 5.0}, ayer) + horarias({h: 7.0 + h * 0.3 for h in range(24)})
    r = ob.temperaturas(lecturas, [(datetime(2026, 10, 13, 12, tzinfo=timezone.utc), "tmin", 5.0)], DIA)
    assert r["tmin"] == 7.0 and r["fuente"]["tmin"] == "lecturas"


def test_maxima_synop_de_las_00utc_esta_toda_dentro_del_dia():
    lecturas = horarias({h: 15.0 for h in range(24)})
    tmax = (datetime(2026, 10, 14, 0, tzinfo=timezone.utc), "tmax", 17.2)    # 09 a 21 hs
    assert ob.temperaturas(lecturas, [tmax], DIA)["tmax"] == 17.2


# ---------------------------------------------------------------------------
# Precipitación
# ---------------------------------------------------------------------------
def test_lluvia_synop_por_ww_y_por_cantidades():
    reportes = [local(h) for h in range(24)]
    # Llovizna a las 10: alcanza el ww (aunque la cantidad sea traza).
    r = ob.lluvia_synop([], [(local(10), 51)], reportes, DIA)
    assert r["pp"] is True and "ww 51" in r["evidencias"][0]
    # 2 mm en las 6 h hasta las 15 hs (todo dentro del día).
    assert ob.lluvia_synop([(local(15), 6, 2.0)], [], reportes, DIA)["pp"] is True
    # Día sin nada.
    assert ob.lluvia_synop([(local(15), 6, 0.0)], [(local(10), 2)], reportes, DIA)["pp"] is False


def test_lluvia_de_24h_de_las_9_no_se_asigna_entera_al_dia():
    # Los 24 h de las 9 del 14 cubren de 9 del 13 a 9 del 14: solo 62 % del 13.
    reportes = [local(h) for h in range(24)]
    r = ob.lluvia_synop([(local(9, DIA + timedelta(days=1)), 24, 5.0)], [], reportes, DIA)
    assert r["pp"] is False


def test_lluvia_synop_sin_reportes_suficientes_es_desconocida():
    assert ob.lluvia_synop([], [], [local(h) for h in range(10)], DIA)["pp"] is None


def test_lluvia_ema():
    lect = [(local(h, m=m), 0.0) for h in range(24) for m in (0, 15, 30, 45)]
    lect[40] = (lect[40][0], 0.2)
    lect[41] = (lect[41][0], 0.4)
    r = ob.lluvia_ema(lect, DIA)
    assert (r["pp"], r["mm"], r["periodos"]["mañana"]) == (True, 0.6, 0.6)


# ---------------------------------------------------------------------------
# Viento
# ---------------------------------------------------------------------------
def test_viento_del_dia():
    lect = [(local(h), 3.0, 135) for h in range(16)] + [(local(h), 7.5, 180) for h in range(16, 22)]
    lect += [(local(22), 0.0, 0), (local(23), 0.0, 0)]
    r = ob.viento(lect, DIA)
    assert r["predominante"] == "SE" and r["sectores"] == {"SE": 16, "S": 6} and r["calmas"] == 2
    assert (r["max_kmh"], r["intensidad"]) == (27.0, "MODERADOS")


def test_viento_ema_se_lleva_a_valores_horarios():
    # Cada 5 minutos, viento de 350 y 10 grados: el promedio es norte, no sur.
    lect = [(local(h, m=m), 2.0, 350 if m % 10 else 10) for h in range(24) for m in range(0, 60, 5)]
    r = ob.viento(lect, DIA)
    assert r["predominante"] == "N" and r["completitud"] == 1.0


def test_categorias_de_viento_del_equipo():
    assert [ob.categoria_viento(k) for k in (5, 22, 22.1, 29, 35, 40)] == \
        ["LEVES", "LEVES", "MODERADOS", "MODERADOS", "REGULARES", "FUERTES"]


# ---------------------------------------------------------------------------
# Pronóstico
# ---------------------------------------------------------------------------
def test_derivar_pronostico_como_la_hoja_del_equipo():
    p = json.loads((Path(__file__).parent / "datos/pronostico_laplata.json").read_text())
    sabado, domingo = (d["period_forecasts"] for d in p["daily_forecasts"][:2])
    s = pronostico.derivar(sabado)
    assert (s["pp"], s["pp_tipo"], s["viento_dirs"], s["viento_int"]) == (True, "lluvias", ["SE", "S"], "LEVES")
    d = pronostico.derivar(domingo)
    assert (d["pp"], d["viento_dirs"], d["tmin_periodo"]) == (False, ["E", "SE"], "mañana")


def test_intensidad_mayor_nombrada():
    assert pronostico.intensidad("leves a moderados") == "MODERADOS"
    assert pronostico.intensidad("Fuerte con ráfagas") == "FUERTES"
    assert pronostico.direcciones("sudeste rotando al sur") == {"SE", "S"}


# ---------------------------------------------------------------------------
# Puntajes
# ---------------------------------------------------------------------------
def test_contingencia():
    pares = [(True, True)] * 6 + [(True, False)] * 2 + [(False, True)] * 3 + [(False, False)] * 19
    r = puntajes.contingencia(pares)
    assert (r["a"], r["b"], r["c"], r["d"]) == (6, 2, 3, 19)
    assert (r["pod"], r["far"], r["csi"], r["bias"], r["pc"]) == (0.67, 0.25, 0.55, 0.89, 0.83)
    assert r["hss"] == 0.59   # 2(6·19-2·3)/((9)(22)+(8)(21)) = 216/366


def test_continuos():
    r = puntajes.continuos([(20, 22), (15, 14), (10, 10), (None, 3)])
    assert (r["n"], r["mae"], r["sesgo"], r["dentro_2"]) == (3, 1.0, -0.33, 100)


def test_pares_y_resumen_con_persistencia():
    cap = datetime(2026, 10, 12, 11, tzinfo=timezone.utc)
    dias = []
    observado = {}
    for i in range(5):
        f = date(2026, 10, 10) + timedelta(days=i)
        dias.append({"ciudad": "Tandil", "fecha_pronostico": f - timedelta(days=1), "capturado": cap,
                     "publicado_por": "Ana", "es_correccion": False, "fecha": f, "plazo_dias": 1,
                     "temp_min": 8, "temp_max": 20 + i, "temp_min_suburbana": None, "temp_max_suburbana": None,
                     "periodos": [{"period": "tarde", "precipitation_description": "lluvias" if i % 2 else None,
                                   "wind_direction": "norte", "wind_intensity": "leves"}]})
    for i in range(-2, 5):
        f = date(2026, 10, 10) + timedelta(days=i)
        observado[("Tandil", f, "tmax_urbana")] = {"valor": 21 + i * 2, "estacion_id": "omm_87645"}
        observado[("Tandil", f, "pp")] = {"categoria": "SI" if i % 2 else "NO"}
        observado[("Tandil", f, "viento_dir")] = {"categoria": "N"}
        observado[("Tandil", f, "viento_int")] = {"categoria": "LEVES", "valor": 15}
    # Una corrección posterior de la emisión del 12: con 'final' se usa esa.
    corr = dict(dias[3], capturado=cap + timedelta(hours=3), temp_max=99)
    filas = sorted(puntajes.armar_pares(puntajes.elegir_emisiones(dias + [corr], "final"), observado), key=lambda f: f["fecha"])
    assert [f["tmax_urbana"]["pron"] for f in filas] == [20, 21, 22, 99, 24]
    orig = puntajes.armar_pares(puntajes.elegir_emisiones(dias + [corr], "original"), observado)
    assert [f["tmax_urbana"]["pron"] for f in orig] == [20, 21, 22, 23, 24]

    r = puntajes.resumen(orig)[1]
    # Errores 20-21, 21-23, 22-25, 23-27, 24-29 -> 1,2,3,4,5
    assert (r["tmax_urbana"]["mae"], r["tmax_urbana"]["sesgo"]) == (3.0, -3.0)
    # Persistencia (lo observado el día antes de emitir): error de 4 grados siempre.
    assert r["tmax_urbana"]["persistencia"]["mae"] == 4.0
    assert r["tmax_urbana"]["destreza"] == 0.25
    assert r["pp"]["pc"] == 1.0 and r["pp"]["persistencia"]["pc"] == 1.0
    assert r["viento_dir"]["pct"] == 100 and r["viento_int"]["pct"] == 100


# ---------------------------------------------------------------------------
# Historial desde la hoja del equipo
# ---------------------------------------------------------------------------
def test_hoja_se_agrupa_por_emision_nominal():
    from meteo.ingesta.fuentes import hoja_verificacion as hoja
    texto = (Path(__file__).parent / "datos/hoja_verificacion.csv").read_text(encoding="utf-8")
    em = {(e["city"], e["forecast_date"]): e for e in hoja.leer_csv(texto)}
    junin = em[("Junín", "2026-08-12")]
    assert [d["date"] for d in junin["daily_forecasts"]] == ["2026-08-13", "2026-08-14", "2026-08-15"]
    # Mar del Plata se publicó el 13 a la noche como pronóstico del 14:
    # Día 2 (plazo 1) = el 15. La emisión nominal es la del 14.
    mdp = em[("Mar del Plata", "2026-08-14")]
    assert mdp["publicado_el"] == ["2026-08-13"]
    dias = {d["fecha"].isoformat(): d["plazo_dias"] for d in pronosticos.desarmar(mdp)}
    assert dias == {"2026-08-15": 1}


def test_hoja_se_deriva_igual_que_un_pronostico_capturado():
    from meteo.ingesta.fuentes import hoja_verificacion as hoja
    texto = (Path(__file__).parent / "datos/hoja_verificacion.csv").read_text(encoding="utf-8")
    em = {(e["city"], e["forecast_date"]): e for e in hoja.leer_csv(texto)}
    dia3 = em[("Junín", "2026-08-12")]["daily_forecasts"][2]
    d = pronostico.derivar(dia3["period_forecasts"])
    assert (d["pp"], d["viento_dirs"], d["viento_int"], d["tormenta"]) == (True, ["SW", "W"], "MODERADOS", True)
    seco = pronostico.derivar(em[("La Plata", "2026-10-11")]["daily_forecasts"][0]["period_forecasts"])
    assert (seco["pp"], seco["viento_dirs"], seco["viento_int"]) == (False, ["E", "SE"], "LEVES")
    assert em[("La Plata", "2026-10-11")]["es_correccion"] is True


def test_publicado_la_noche_anterior_se_marca_y_se_puede_separar():
    base = {"ciudad": "Junín", "capturado": datetime(2026, 8, 13, 23, tzinfo=timezone.utc), "publicado_por": "A",
            "es_correccion": False, "plazo_dias": 1, "temp_min": 5, "temp_max": 12,
            "temp_min_suburbana": None, "temp_max_suburbana": None, "periodos": []}
    en_el_dia = dict(base, fecha_pronostico=date(2026, 8, 13), fecha=date(2026, 8, 14), publicado_el=date(2026, 8, 13))
    noche = dict(base, fecha_pronostico=date(2026, 8, 14), fecha=date(2026, 8, 15), publicado_el=date(2026, 8, 13))
    filas = puntajes.armar_pares([en_el_dia, noche], {})
    assert [f["anticipado"] for f in filas] == [False, True]
    assert all(f["plazo"] == 1 for f in filas)   # el plazo es el del bloque en los dos
    assert [f["fecha"] for f in puntajes.filtrar_publicacion(filas, "noche_anterior")] == [date(2026, 8, 15)]
    assert [f["fecha"] for f in puntajes.filtrar_publicacion(filas, "en_el_dia")] == [date(2026, 8, 14)]


def test_estacion_que_no_reporta_de_noche():
    # Reportes cada 3 h de 09 a 21 hs (12 a 00 UTC), como algunas del SMN.
    manana = DIA + timedelta(days=1)
    lect = horarias({9: 8.0, 12: 14.0, 15: 17.5, 18: 15.0, 21: 12.0})
    ext = [(datetime(2026, 10, 13, 12, tzinfo=timezone.utc), "tmin", 5.2),    # 21-09: mínima de la madrugada
           (datetime(2026, 10, 14, 0, tzinfo=timezone.utc), "tmax", 18.1),    # 09-21: máxima del día
           (datetime(2026, 10, 14, 12, tzinfo=timezone.utc), "tmin", 3.0)]    # 21-09 del día siguiente: NO
    r = ob.temperaturas(lect + horarias({9: 6.0}, manana), ext, DIA)
    assert (r["tmax"], r["tmin"], r["tmax_ok"], r["tmin_ok"]) == (18.1, 5.2, True, True)
    # Sin los grupos, con 5 lecturas el día no alcanza.
    r = ob.temperaturas(lect, [], DIA)
    assert (r["tmax_ok"], r["tmin_ok"]) == (False, False)


def test_dia_sin_lluvia_por_cantidades_en_cero():
    # Lluvia de 12 h a las 21 hs (09-21) y de 24 h a las 9 (09-09): en cero,
    # y además la de 24 h de las 9 del mismo día (cubre 00-09): día seco.
    reportes = [local(h) for h in (9, 12, 15, 18, 21)]
    cant = [(local(9), 24, 0.0), (local(21), 12, 0.0), (local(9, DIA + timedelta(days=1)), 24, 0.0)]
    assert ob.lluvia_synop(cant, [], reportes, DIA)["pp"] is False
    # Solo la de 12 h (09-21): no alcanza para decir que no llovió.
    assert ob.lluvia_synop(cant[1:2], [], reportes, DIA)["pp"] is None


def test_bolivar_dia_seco_con_ir3_y_grupos_de_6h():
    """Día real de Bolívar (10/08/2026): reporta de 09 a 21 hs. Las 24 h de
    las 12 UTC del 10 y del 11 en 0 (iR=3) + 6 h de las 18 y 00 UTC en 0."""
    from meteo.ingesta.synop import decodificar
    msjs = {
        datetime(2026, 8, 10, 12, tzinfo=timezone.utc): "AAXX 10123 87640 31960 00000 10015 21007 30250 40370 52017 700// 333 10050 21022 30104=",
        datetime(2026, 8, 10, 18, tzinfo=timezone.utc): "AAXX 10183 87640 12565 31403 10110 20010 30238 4//// 57020 60001 83100 333 56300 83825=",
        datetime(2026, 8, 11, 0, tzinfo=timezone.utc): "AAXX 11003 87640 12960 00000 10038 20007 30236 40355 52010 60001 333 10113 20015=",
        datetime(2026, 8, 11, 12, tzinfo=timezone.utc): "AAXX 11123 87640 31965 60000 11004 21010 30218 40338 50001 702// 80006 333 10038 21015 34102 56006 86270=",
    }
    obs = [o for ts, m in msjs.items() for o in decodificar(m, ts, "omm_87640")]
    cant = [(o.ts, int(o.variable.split("_")[1][:-1]), o.valor) for o in obs if o.variable.startswith("precip_")]
    r = ob.lluvia_synop(cant, [], list(msjs), date(2026, 8, 10))
    assert r["pp"] is False
