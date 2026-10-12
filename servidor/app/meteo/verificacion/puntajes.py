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

import numpy as np

from .pronostico import INTENSIDADES, derivar

TEMPERATURAS = {
    "tmax_urbana": "temp_max", "tmin_urbana": "temp_min",
    "tmax_suburbana": "temp_max_suburbana", "tmin_suburbana": "temp_min_suburbana",
}


PUBLICACIONES = ("todas", "en_el_dia", "noche_anterior")


def filtrar_publicacion(filas, publicacion):
    if publicacion == "en_el_dia":
        return [f for f in filas if not f["anticipado"]]
    if publicacion == "noche_anterior":
        return [f for f in filas if f["anticipado"]]
    return filas


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
        publicado = d.get("publicado_el") or d["fecha_pronostico"]
        fila = {
            "ciudad": c, "fecha": f, "plazo": d["plazo_dias"], "fecha_emision": d["fecha_pronostico"],
            "publicado_el": publicado,
            # Publicado la noche anterior: el "Día 1" de un pronóstico del
            # martes se publicó el lunes. El plazo sigue siendo el del bloque
            # (comparable entre pronosticadores), pero la antelación real es
            # un día más: se marca para poder separarlos.
            "anticipado": publicado < d["fecha_pronostico"],
            "pronosticador": d.get("publicado_por"), "es_correccion": d.get("es_correccion"),
            # None = no hay datos para etiquetar ese día; [] = día sin nada especial.
            "etiquetas": ((obs("etiquetas").get("detalle") or {}).get("etiquetas")
                          if obs("etiquetas") else None),
            "etiquetas_evidencia": (obs("etiquetas").get("detalle") or {}).get("evidencias") or {},
        }
        for el, campo in TEMPERATURAS.items():
            fila[el] = {"pron": d.get(campo), "obs": obs(el).get("valor"), "pers": obs(el, ayer).get("valor"),
                        "estacion": obs(el).get("estacion_id"), "periodo": obs(el).get("categoria")}
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
# ---------------------------------------------------------------------------
# Confiabilidad: semáforo e intervalos de confianza
# ---------------------------------------------------------------------------
# Semáforo según cuántos casos hay. Para la lluvia cuentan los días CON
# lluvia (los "eventos"): con 60 días y 5 de lluvia, el HSS se apoya en 5.
NIVELES = ((30, 100), (10, 30))   # (días: indicativo desde, robusto desde), (eventos: ídem)
REMUESTREOS = 1000
CONFIANZA = 0.90


def nivel(n, eventos=None):
    if eventos is not None:
        ind, rob = NIVELES[1]
        if eventos < ind:
            return "exploratorio"
        return "robusto" if eventos >= rob and n >= NIVELES[0][1] else "indicativo"
    ind, rob = NIVELES[0]
    return "exploratorio" if n < ind else "indicativo" if n < rob else "robusto"


def _indices(n, semilla, b=REMUESTREOS):
    # Semilla fija: la misma consulta da siempre el mismo intervalo.
    return np.random.default_rng(semilla).integers(0, n, size=(b, n))


def _intervalo(valores):
    a = (1 - CONFIANZA) / 2
    lo, hi = np.quantile(valores, [a, 1 - a])
    return [round(float(lo), 2), round(float(hi), 2)]


def ic_errores(errores):
    """Bootstrap: se rearma la muestra al azar (con repetición) muchas
    veces y se mira cuánto varían el MAE y el sesgo. Devuelve el rango del
    90 % central. Con menos de 5 casos no tiene sentido."""
    e = np.asarray(errores, dtype=float)
    if len(e) < 5:
        return None
    m = e[_indices(len(e), 1)]
    return {"mae": _intervalo(np.abs(m).mean(axis=1)), "sesgo": _intervalo(m.mean(axis=1))}


def ic_diferencia_mae(e1, e2):
    """¿El MAE del grupo 1 es distinto del del grupo 2 (ej. noches con mínima
    nocturna contra el resto)? Intervalo de MAE1 − MAE2: si incluye el 0,
    la diferencia puede ser azar."""
    a, b = np.abs(np.asarray(e1, dtype=float)), np.abs(np.asarray(e2, dtype=float))
    if len(a) < 5 or len(b) < 5:
        return None
    d = a[_indices(len(a), 2)].mean(axis=1) - b[_indices(len(b), 3)].mean(axis=1)
    lo, hi = _intervalo(d)
    return {"dif": round(float(a.mean() - b.mean()), 2), "ic": [lo, hi], "puede_ser_azar": lo <= 0 <= hi}


def ic_vs_persistencia(e_pron, e_pers):
    """Mismos días (pares): ¿el pronóstico erra menos que la persistencia?
    Intervalo de MAE_persistencia − MAE_pronóstico (positivo = gana el
    pronóstico)."""
    d = np.abs(np.asarray(e_pers, dtype=float)) - np.abs(np.asarray(e_pron, dtype=float))
    if len(d) < 5:
        return None
    lo, hi = _intervalo(d[_indices(len(d), 4)].mean(axis=1))
    return {"dif": round(float(d.mean()), 2), "ic": [lo, hi], "puede_ser_azar": lo <= 0 <= hi}


def ic_hss(pares):
    p = np.array([bool(a) for a, o in pares if a is not None and o is not None])
    o = np.array([bool(o) for a, o in pares if a is not None and o is not None])
    if len(p) < 5:
        return None
    i = _indices(len(p), 5)
    P, O = p[i], o[i]
    a = (P & O).sum(1); b = (P & ~O).sum(1); c = (~P & O).sum(1); d = (~P & ~O).sum(1)
    den = (a + c) * (c + d) + (a + b) * (b + d)
    with np.errstate(invalid="ignore", divide="ignore"):
        h = np.where(den > 0, 2 * (a * d - b * c) / den, np.nan)
    h = h[~np.isnan(h)]
    return _intervalo(h) if len(h) else None


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


def minimas_nocturnas(filas, observado, desde, hasta):
    """¿Cuántos días la mínima fue NOCTURNA (entre las 18 y las 24 hs), cuáles,
    y si el pronóstico erra más esos días?

    observado: {(ciudad, fecha, elemento): {"valor", "categoria", "detalle"...}}
    Para cada ámbito (urbana/suburbana): conteo por período del día en que
    ocurrió la mínima, la lista de días nocturnos (con la mínima de la
    madrugada de ese mismo día, para ver la diferencia) y el error de la
    mínima por plazo separando días nocturnos del resto."""
    salida = {}
    for ambito in ("urbana", "suburbana"):
        el = f"tmin_{ambito}"
        filas_obs = sorted(((f, o) for (c, f, e), o in observado.items() if e == el and desde <= f <= hasta),
                           key=lambda x: x[0])
        if not filas_obs:
            continue
        conteo = {p: 0 for p in ("madrugada", "mañana", "tarde", "noche")}
        for _, o in filas_obs:
            if o.get("categoria") in conteo:
                conteo[o["categoria"]] += 1
        pron = {(f["fecha"], f["plazo"]): f[el]["pron"] for f in filas}
        dias = []
        for fecha, o in filas_obs:
            if o.get("categoria") != "noche":
                continue
            det = o.get("detalle") or {}
            per = det.get("periodos") or {}
            dias.append({"fecha": fecha, "tmin": o["valor"], "hora": det.get("tmin_hora"),
                         "min_madrugada": (per.get("madrugada") or {}).get("min"),
                         "min_manana": (per.get("mañana") or {}).get("min"),
                         "estacion": o.get("estacion_id"),
                         "pronosticos": {p: pron.get((fecha, p)) for p in range(4) if pron.get((fecha, p)) is not None}})
        errores = {}
        for p in sorted({f["plazo"] for f in filas}):
            v = [f[el] for f in filas if f["plazo"] == p and f[el]["pron"] is not None and f[el]["obs"] is not None]
            en = [x["pron"] - x["obs"] for x in v if x["periodo"] == "noche"]
            er = [x["pron"] - x["obs"] for x in v if x["periodo"] != "noche"]
            errores[p] = {"nocturna": continuos([(x["pron"], x["obs"]) for x in v if x["periodo"] == "noche"]),
                          "resto": continuos([(x["pron"], x["obs"]) for x in v if x["periodo"] != "noche"]),
                          "nivel": nivel(len(en)), "comparacion": ic_diferencia_mae(en, er)}
        salida[ambito] = {"dias_con_dato": len(filas_obs), "periodos": conteo,
                          "estaciones": sorted({o.get("estacion_id") for _, o in filas_obs if o.get("estacion_id")}),
                          "nocturnas": dias, "errores": errores}
    return salida


def filtrar_etiqueta(filas, etiqueta):
    return [f for f in filas if etiqueta in (f.get("etiquetas") or [])] if etiqueta else filas


def resumen_situaciones(filas):
    """Para cada etiqueta, plazo y temperatura: el error los días CON esa
    situación contra el resto de los días etiquetados, y si la diferencia
    puede ser azar. Más la proporción de aciertos de lluvia."""
    from .etiquetas import ETIQUETAS
    etiquetadas = [f for f in filas if f.get("etiquetas") is not None]
    salida = {}
    for et in ETIQUETAS:
        con = [f for f in etiquetadas if et in f["etiquetas"]]
        if not con:
            continue
        sin = [f for f in etiquetadas if et not in f["etiquetas"]]
        por_plazo = {}
        for p in sorted({f["plazo"] for f in con}):
            cp, sp = [f for f in con if f["plazo"] == p], [f for f in sin if f["plazo"] == p]
            r = {"dias": len(cp), "nivel": nivel(len(cp))}
            for el in TEMPERATURAS:
                e1 = [f[el]["pron"] - f[el]["obs"] for f in cp if f[el]["pron"] is not None and f[el]["obs"] is not None]
                e2 = [f[el]["pron"] - f[el]["obs"] for f in sp if f[el]["pron"] is not None and f[el]["obs"] is not None]
                if e1:
                    r[el] = {"con": continuos([(e, 0) for e in e1]), "sin": continuos([(e, 0) for e in e2]),
                             "comparacion": ic_diferencia_mae(e1, e2)}
            pp1 = aciertos([(f["pp"]["pron"], f["pp"]["obs"]) for f in cp], lambda a, b: a == b)
            pp2 = aciertos([(f["pp"]["pron"], f["pp"]["obs"]) for f in sp], lambda a, b: a == b)
            r["pp"] = {"con": pp1, "sin": pp2}
            por_plazo[p] = r
        salida[et] = por_plazo
    return salida


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
            pron["nivel"] = nivel(pron["n"])
            pron["ic"] = ic_errores([x["pron"] - x["obs"] for x in v])
            pron["vs_persistencia"] = ic_vs_persistencia([x["pron"] - x["obs"] for x in igual],
                                                         [x["pers"] - x["obs"] for x in igual])
            r[el] = pron
        v = [f["pp"] for f in fp]
        r["pp"] = contingencia([(x["pron"], x["obs"]) for x in v])
        r["pp"]["nivel"] = nivel(r["pp"]["n"], eventos=r["pp"]["a"] + r["pp"]["c"])
        r["pp"]["ic_hss"] = ic_hss([(x["pron"], x["obs"]) for x in v])
        ref = contingencia([(x["pers"], x["obs"]) for x in v if x["pron"] is not None])
        r["pp"]["persistencia"] = ref
        v = [f["viento_dir"] for f in fp if f["viento_dir"]["pron"]]
        r["viento_dir"] = aciertos([(x["pron"], x["obs"]) for x in v], dir_acierta)
        r["viento_dir"]["nivel"] = nivel(r["viento_dir"]["n"])
        r["viento_dir"]["persistencia"] = aciertos([(x["pers"], x["obs"]) for x in v], dir_acierta)
        v = [f["viento_int"] for f in fp]
        r["viento_int"] = aciertos([(x["pron"], x["obs"]) for x in v], int_exacta)
        r["viento_int"]["nivel"] = nivel(r["viento_int"]["n"])
        r["viento_int"]["cerca"] = aciertos([(x["pron"], x["obs"]) for x in v], int_cerca).get("pct")
        r["viento_int"]["persistencia"] = aciertos(
            [(x["pers"], x["obs"]) for x in v if x["pron"]], int_exacta)
        salida[p] = r
    return salida
