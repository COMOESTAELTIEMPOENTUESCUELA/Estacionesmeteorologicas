"""
Pares pronóstico-observación y medidas de error.

Para entender las medidas:

  Temperatura (variable continua), con e = pronóstico - observado:
    MAE   = promedio de |e|: cuánto se erra en promedio, sin signo.
    sesgo = promedio de e: si se pronostica sistemáticamente de más (+)
            o de menos (-).
    RMSE  = raíz del promedio de e²: como el MAE pero castiga más los
            errores grandes.

  Precipitación sí/no (tabla de contingencia):
                       llovió     no llovió
    se pronosticó        a (acierto)   b (falsa alarma)
    no se pronosticó     c (sorpresa)  d (acierto negativo)
    POD = a/(a+c)   de los días que llovió, cuántos se anunciaron.
    FAR = b/(a+b)   de los días que se anunció, en cuántos NO llovió.
    CSI = a/(a+b+c) aciertos sin contar los días secos fáciles.
    BIAS = (a+b)/(a+c) >1: se anuncia lluvia más seguido de lo que llueve.
    HSS (Heidke): aciertos por encima de los que se lograrían por azar.
            1 = perfecto, 0 = igual que tirar una moneda cargada con la
            climatología, <0 = peor que eso.

  Referencia (persistencia): "mañana va a pasar lo mismo que ayer". Un
  pronóstico tiene VALOR si le gana. Destreza = 1 - error/error_referencia:
  0,3 = 30 % menos error que la persistencia.
"""
import math
from datetime import timedelta

from .pronostico import INTENSIDADES, derivar

TEMPERATURAS = {
    "tmax_urbana": "temp_max", "tmin_urbana": "temp_min",
    "tmax_suburbana": "temp_max_suburbana", "tmin_suburbana": "temp_min_suburbana",
}


def elegir_emisiones(dias, version="final"):
    """Si una emisión se corrigió, hay varias para el mismo (ciudad, fecha de
    emisión). 'final' usa la última capturada; 'original', la primera."""
    elegida = {}
    for d in dias:
        k = (d["ciudad"], d["fecha_pronostico"])
        actual = elegida.get(k)
        if actual is None or (d["capturado"] > actual if version == "final" else d["capturado"] < actual):
            elegida[k] = d["capturado"]
    return [d for d in dias if elegida[(d["ciudad"], d["fecha_pronostico"])] == d["capturado"]]


def armar_pares(dias, observado):
    """dias: filas de pronostico_dia + emisión. observado: {(ciudad, fecha,
    elemento): {"valor", "categoria", "estacion_id"}}.
    Devuelve una fila por día pronosticado, con lo pronosticado, lo observado
    y la persistencia (lo observado el día anterior a la emisión)."""
    filas = []
    for d in dias:
        c, f = d["ciudad"], d["fecha"]
        ayer = d["fecha_pronostico"] - timedelta(days=1)
        der = derivar(d["periodos"])
        obs = lambda el, dia=f: observado.get((c, dia, el)) or {}
        fila = {
            "ciudad": c, "fecha": f, "plazo": d["plazo_dias"], "fecha_emision": d["fecha_pronostico"],
            "pronosticador": d.get("publicado_por"), "es_correccion": d.get("es_correccion"),
        }
        for el, campo in TEMPERATURAS.items():
            fila[el] = {"pron": d.get(campo), "obs": obs(el).get("valor"), "pers": obs(el, ayer).get("valor"),
                        "estacion": obs(el).get("estacion_id")}
        fila["tmin_periodo"] = {"pron": der["tmin_periodo"], "obs": obs("tmin_urbana").get("categoria")
                                or obs("tmin_suburbana").get("categoria")}
        si_no = lambda o: None if not o else o.get("categoria") == "SI"
        fila["pp"] = {"pron": der["pp"], "tipo": der["pp_tipo"], "obs": si_no(obs("pp")),
                      "pers": si_no(obs("pp", ayer)), "estacion": obs("pp").get("estacion_id")}
        pers_dir = obs("viento_dir", ayer).get("categoria")
        fila["viento_dir"] = {"pron": der["viento_dirs"], "obs": obs("viento_dir").get("categoria"),
                              "pers": [pers_dir] if pers_dir else None,
                              "estacion": obs("viento_dir").get("estacion_id")}
        fila["viento_int"] = {"pron": der["viento_int"], "obs": obs("viento_int").get("categoria"),
                              "obs_kmh": obs("viento_int").get("valor"),
                              "pers": obs("viento_int", ayer).get("categoria")}
        filas.append(fila)
    return filas


# ---------------------------------------------------------------------------
def continuos(pares):
    """pares: [(pronóstico, observado)] numéricos."""
    e = [p - o for p, o in pares if p is not None and o is not None]
    if not e:
        return {"n": 0}
    n = len(e)
    return {"n": n, "mae": round(sum(map(abs, e)) / n, 2), "sesgo": round(sum(e) / n, 2),
            "rmse": round(math.sqrt(sum(x * x for x in e) / n), 2),
            "dentro_2": round(100 * sum(1 for x in e if abs(x) <= 2) / n)}


def contingencia(pares):
    """pares: [(pronosticó_sí, ocurrió)] booleanos."""
    pares = [(p, o) for p, o in pares if p is not None and o is not None]
    a = sum(1 for p, o in pares if p and o)
    b = sum(1 for p, o in pares if p and not o)
    c = sum(1 for p, o in pares if not p and o)
    d = sum(1 for p, o in pares if not p and not o)
    n = a + b + c + d
    div = lambda x, y: round(x / y, 2) if y else None
    hss_den = (a + c) * (c + d) + (a + b) * (b + d)
    return {"n": n, "a": a, "b": b, "c": c, "d": d,
            "pod": div(a, a + c), "far": div(b, a + b), "csi": div(a, a + b + c),
            "bias": div(a + b, a + c), "pc": div(a + d, n),
            "hss": div(2 * (a * d - b * c), hss_den)}


def aciertos(pares, acierta):
    pares = [(p, o) for p, o in pares if p is not None and o is not None]
    if not pares:
        return {"n": 0}
    return {"n": len(pares), "pct": round(100 * sum(1 for p, o in pares if acierta(p, o)) / len(pares))}


def dir_acierta(pron, obs):
    return obs in pron


def int_exacta(pron, obs):
    return pron == obs


def int_cerca(pron, obs):
    return abs(INTENSIDADES.index(pron) - INTENSIDADES.index(obs)) <= 1


def destreza(error, error_ref):
    if error is None or not error_ref:
        return None
    return round(1 - error / error_ref, 2)


def resumen(filas):
    """Medidas por elemento y plazo, con la persistencia evaluada sobre los
    MISMOS días (si no, la comparación no es justa)."""
    plazos = sorted({f["plazo"] for f in filas})
    salida = {}
    for p in plazos:
        fp = [f for f in filas if f["plazo"] == p]
        r = {}
        for el in TEMPERATURAS:
            v = [f[el] for f in fp if f[el]["pron"] is not None and f[el]["obs"] is not None]
            pron = continuos([(x["pron"], x["obs"]) for x in v])
            ref = continuos([(x["pers"], x["obs"]) for x in v if x["pers"] is not None])
            # La destreza solo se calcula si la persistencia existe en esos mismos días.
            igual = [x for x in v if x["pers"] is not None]
            mae_mismos = continuos([(x["pron"], x["obs"]) for x in igual]).get("mae")
            pron["destreza"] = destreza(mae_mismos, ref.get("mae"))
            pron["persistencia"] = ref
            r[el] = pron
        v = [f["pp"] for f in fp]
        r["pp"] = contingencia([(x["pron"], x["obs"]) for x in v])
        ref = contingencia([(x["pers"], x["obs"]) for x in v if x["pron"] is not None])
        r["pp"]["persistencia"] = ref
        v = [f["viento_dir"] for f in fp if f["viento_dir"]["pron"]]
        r["viento_dir"] = aciertos([(x["pron"], x["obs"]) for x in v], dir_acierta)
        r["viento_dir"]["persistencia"] = aciertos([(x["pers"], x["obs"]) for x in v], dir_acierta)
        v = [f["viento_int"] for f in fp]
        r["viento_int"] = aciertos([(x["pron"], x["obs"]) for x in v], int_exacta)
        r["viento_int"]["cerca"] = aciertos([(x["pron"], x["obs"]) for x in v], int_cerca).get("pct")
        r["viento_int"]["persistencia"] = aciertos(
            [(x["pers"], x["obs"]) for x in v if x["pron"]], int_exacta)
        salida[p] = r
    return salida
