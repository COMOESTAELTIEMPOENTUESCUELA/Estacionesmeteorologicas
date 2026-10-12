"""
Etiquetas de situación: qué tipo de día fue, detectado de los SYNOP horarios.

Sirven para preguntar, por ejemplo, "¿la mínima falla más las noches
despejadas y en calma?". Un día puede tener varias etiquetas. Los criterios
(y los umbrales, en UMBRALES) están explicados en docs/05-verificacion.md y
en la pestaña Verificación ("Cómo funciona").

Cada etiqueta que se detecta guarda una EVIDENCIA en palabras (ej. "presión
+4,1 hPa y temperatura −6,0 °C entre las 15 y las 21 hs, viento rota al SW")
para poder controlarla en Ogimet. Las que no se pudieron evaluar por falta
de datos (ej. la noche radiativa en una estación que no reporta de noche)
quedan en "no_evaluadas": no es lo mismo "no pasó" que "no se sabe".
"""
from datetime import timedelta, timezone

ETIQUETAS = {
    "frente_frio": "Paso de frente frío",
    "noche_radiativa": "Noche radiativa (despejada y en calma)",
    "adveccion_calida": "Viento norte (advección cálida)",
    "sudestada": "Sudestada",
    "cubierto": "Día cubierto",
    "niebla": "Niebla",
    "tormenta": "Tormenta",
    "lluvia_intensa": "Lluvia intensa",
    "viento_fuerte": "Viento fuerte",
    "helada": "Helada",
    "helada_agro": "Helada agrometeorológica",
    "caida_presion": "Caída fuerte de presión",
    "minima_nocturna": "Mínima nocturna",
}

UMBRALES = {
    "frente_horas": 6,            # ventana para el salto de presión y temperatura
    "frente_dp_hpa": 3.0,         # sube la presión al menos esto
    "frente_dt_c": 4.0,           # baja la temperatura al menos esto
    "radiativa_octas": 2,         # nubosidad máxima
    "radiativa_viento_ms": 2.0,   # viento menor a esto
    "radiativa_fraccion": 0.7,    # de las horas de la noche (21 a 07 hs)
    "norte_horas": 6, "norte_viento_ms": 3.0,
    "sudestada_horas": 6, "sudestada_viento_kmh": 30.0,
    "cubierto_octas": 7.0,        # nubosidad media de 09 a 18 hs
    "lluvia_intensa_mm": 20.0,
    "viento_fuerte_kmh": 40.0,
    "helada_c": 0.0, "helada_agro_c": 3.0,
    "caida_presion_hpa": 8.0,     # en 24 horas
}

WW_NIEBLA = set(range(40, 50))
WW_TORMENTA = {17, 29} | set(range(91, 100))
RUMBOS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _hora(t):
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _serie(pares):
    return {_hora(t): v for t, v in pares if v is not None}


def _local(t):
    from .observado import hora_local
    return hora_local(t)


def _rumbo(g):
    return RUMBOS[round(g / 45) % 8]


def _en_sector(g, desde, hasta):
    """¿La dirección g (grados, de dónde viene) está entre desde y hasta?"""
    if g is None or g == 0:
        return False
    g %= 360
    return desde <= g <= hasta if desde <= hasta else (g >= desde or g <= hasta)


def mm_24h_synop(cantidades, dia):
    """Lluvia de 24 h del reporte de las 12 UTC del día siguiente (de 9 a 9)."""
    from .observado import limites
    fin12 = limites(dia)[0].replace(hour=12) + timedelta(days=1)
    return next((mm for t, h, mm in cantidades if h == 24 and _hora(t) == fin12), None)


def etiquetar(est, dia, temp=None, viento=None, mm_24h=None):
    """est: datos de UNA estación (salida de observado._armar, con "presion",
    "nubes" y "vis"). temp y viento: lo ya calculado para ese día.
    Devuelve {"etiquetas": [...], "evidencias": {...}, "no_evaluadas": [...]}."""
    from .observado import limites
    U = UMBRALES
    inicio, fin = limites(dia)
    P, T = _serie(est.get("presion", [])), _serie(est.get("temp", []))
    V = {_hora(t): v for t, v, _ in est.get("viento", []) if v is not None}
    D = {_hora(t): d for t, _, d in est.get("viento", [])}
    N = {h: n for h, n in _serie(est.get("nubes", [])).items() if n <= 8}
    horas_dia = [h for h in sorted(set(P) | set(T) | set(V)) if inicio <= h < fin]
    etiquetas, evid, no_eval = [], {}, []

    def poner(nombre, texto):
        etiquetas.append(nombre)
        evid[nombre] = texto

    # Paso de frente frío: en 6 h sube la presión, baja la temperatura y el
    # viento termina del cuadrante sur (SE a SW).
    pares = [(h, h + timedelta(hours=U["frente_horas"])) for h in horas_dia]
    pares = [(a, b) for a, b in pares if a in P and b in P and a in T and b in T]
    if len(pares) >= 6:
        for a, b in pares:
            dp, dt = P[b] - P[a], T[a] - T[b]
            if (dp >= U["frente_dp_hpa"] and dt >= U["frente_dt_c"]
                    and _en_sector(D.get(b), 112.5, 247.5) and not _en_sector(D.get(a), 112.5, 247.5)):
                poner("frente_frio", f"presión +{dp:.1f} hPa y temperatura −{dt:.1f} °C entre las "
                      f"{_local(a):02d} y las {_local(b):02d} hs; el viento pasa a {_rumbo(D[b])}")
                break
    else:
        no_eval.append("frente_frio")

    # Noche radiativa: de 21 a 07 hs (la noche que termina en la mañana del día).
    noche = [h for h in sorted(set(N) & set(V)) if inicio - timedelta(hours=3) <= h < inicio + timedelta(hours=7)]
    if len(noche) >= 5:
        buenas = [h for h in noche if N[h] <= U["radiativa_octas"] and V[h] < U["radiativa_viento_ms"]]
        if len(buenas) >= U["radiativa_fraccion"] * len(noche):
            poner("noche_radiativa", f"{len(buenas)} de {len(noche)} horas de la noche con ≤ "
                  f"{U['radiativa_octas']} octas y viento < {U['radiativa_viento_ms']:g} m/s")
    else:
        no_eval.append("noche_radiativa")

    viento_dia = [h for h in horas_dia if h in V]
    if len(viento_dia) >= 8:
        norte = [h for h in viento_dia if _en_sector(D.get(h), 337.5, 67.5) and V[h] >= U["norte_viento_ms"]]
        if len(norte) >= U["norte_horas"]:
            poner("adveccion_calida", f"{len(norte)} horas con viento del N o NE de {U['norte_viento_ms']:g} m/s o más")
        sud = [h for h in viento_dia if _en_sector(D.get(h), 67.5, 157.5) and V[h] * 3.6 >= U["sudestada_viento_kmh"]]
        if len(sud) >= U["sudestada_horas"]:
            poner("sudestada", f"{len(sud)} horas con viento del E o SE de {U['sudestada_viento_kmh']:g} km/h o más")
    else:
        no_eval += ["adveccion_calida", "sudestada"]

    diurnas = [N[h] for h in N if inicio + timedelta(hours=9) <= h <= inicio + timedelta(hours=18)]
    if len(diurnas) >= 4:
        media = sum(diurnas) / len(diurnas)
        if media >= U["cubierto_octas"]:
            poner("cubierto", f"nubosidad media de {media:.1f} octas entre las 09 y las 18 hs")
    else:
        no_eval.append("cubierto")

    ww = [(t, c) for t, c in est.get("ww", []) if inicio <= t < fin]
    vis = [(t, v) for t, v in est.get("vis", []) if inicio <= t < fin and v is not None]
    if horas_dia:
        niebla = [t for t, c in ww if c in WW_NIEBLA] + [t for t, v in vis if v < 1000]
        if niebla:
            poner("niebla", f"niebla o visibilidad < 1 km a las {_local(min(niebla)):02d} hs")
        tormenta = [(t, c) for t, c in ww if c in WW_TORMENTA]
        if tormenta:
            poner("tormenta", f"tiempo presente {tormenta[0][1]} a las {_local(tormenta[0][0]):02d} hs")
    else:
        no_eval += ["niebla", "tormenta"]

    if mm_24h is not None:
        if mm_24h >= U["lluvia_intensa_mm"]:
            poner("lluvia_intensa", f"{mm_24h:g} mm en 24 h")
    else:
        no_eval.append("lluvia_intensa")

    if viento and viento.get("max_kmh") is not None:
        if viento["max_kmh"] >= U["viento_fuerte_kmh"]:
            poner("viento_fuerte", f"viento medio de {viento['max_kmh']:g} km/h a las {viento['max_hora']:02d} hs")
    else:
        no_eval.append("viento_fuerte")

    if temp and temp.get("tmin_ok"):
        if temp["tmin"] <= U["helada_c"]:
            poner("helada", f"mínima de {temp['tmin']:g} °C")
        if temp["tmin"] <= U["helada_agro_c"]:
            poner("helada_agro", f"mínima de {temp['tmin']:g} °C")
        if temp.get("tmin_periodo") == "noche":
            poner("minima_nocturna", f"mínima de {temp['tmin']:g} °C a las {temp['tmin_hora']:02d} hs")
    else:
        no_eval += ["helada", "helada_agro", "minima_nocturna"]

    pares24 = [(h - timedelta(hours=24), h) for h in horas_dia if h in P and h - timedelta(hours=24) in P]
    if len(pares24) >= 6:
        a, b = max(pares24, key=lambda x: P[x[0]] - P[x[1]])
        if P[a] - P[b] >= U["caida_presion_hpa"]:
            poner("caida_presion", f"la presión baja {P[a] - P[b]:.1f} hPa en las 24 h hasta las {_local(b):02d} hs")
    else:
        no_eval.append("caida_presion")

    return {"etiquetas": etiquetas, "evidencias": evid, "no_evaluadas": no_eval}
