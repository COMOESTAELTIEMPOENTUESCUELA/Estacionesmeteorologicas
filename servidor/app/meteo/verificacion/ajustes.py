"""
Parámetros de la verificación que un administrador puede cambiar desde la
página (sección Administración), sin tocar el código.

Los valores por defecto están en observado.py. Lo que se cambia se guarda en
la tabla `ajuste` (clave -> valor, con quién y cuándo) y se aplica al
calcular lo observado (observado.calcular). Solo números, cada uno con su
mínimo y máximo: desde la página NO se puede cambiar el código en sí (sería
dejar que alguien ejecute cualquier cosa en el servidor).
"""
import math

from . import observado

# clave, nombre, explicación, mínimo, máximo, paso, unidad
PARAMETROS = [
    ("umbral_pp_mm", "Umbral de lluvia (EMA)",
     "Desde cuántos mm en el día una EMA cuenta el día como lluvioso. Menos que eso: sin precipitación.",
     0.0, 5.0, 0.1, "mm"),
    ("completitud_minima", "Completitud mínima del día",
     "Fracción de las 24 horas con datos para dar por bueno un día (máxima, mínima, día seco). 0,75 = 18 horas.",
     0.3, 1.0, 0.05, ""),
    ("completitud_viento", "Completitud mínima del viento",
     "Fracción de las horas con viento medido para verificar el viento del día. 0,33 = 8 horas (hay estaciones que no reportan de noche).",
     0.1, 1.0, 0.01, ""),
    ("superposicion_lluvia", "Superposición de una cantidad de lluvia con el día",
     "Qué fracción del período de una cantidad del SYNOP (ej. 24 h de 9 a 9) tiene que caer dentro del día para contarla.",
     0.5, 1.0, 0.05, ""),
    ("traza_cuenta", "¿La traza cuenta como lluvia?",
     "Sí: alcanza con que el SYNOP informe lluvia o llovizna, aunque el pluviómetro marque solo traza (\"Ip\", menos de 0,1 mm). "
     "No: una llovizna que dejó solo traza cuenta como día sin precipitación (como la climatología del SMN, desde 0,1 mm).",
     0, 1, 1, "sí/no"),
    ("viento_leves", "Viento: tope de LEVES", "Viento medio horario máximo (km/h) hasta el que el día es LEVES.", 5, 60, 1, "km/h"),
    ("viento_moderados", "Viento: tope de MODERADOS", "Hasta acá, MODERADOS.", 5, 80, 1, "km/h"),
    ("viento_regulares", "Viento: tope de REGULARES", "Hasta acá, REGULARES; más, FUERTES.", 5, 120, 1, "km/h"),
]
CLAVES = {p[0] for p in PARAMETROS}


def por_defecto():
    v = dict(observado.CATEGORIAS_VIENTO_DEFECTO)
    return {"umbral_pp_mm": observado.UMBRAL_PP_MM_DEFECTO, "completitud_minima": observado.COMPLETITUD_MINIMA_DEFECTO,
            "completitud_viento": observado.COMPLETITUD_VIENTO_DEFECTO,
            "superposicion_lluvia": observado.SUPERPOSICION_LLUVIA_DEFECTO,
            "traza_cuenta": 1.0 if observado.TRAZA_CUENTA_DEFECTO else 0.0,
            "viento_leves": v["LEVES"], "viento_moderados": v["MODERADOS"], "viento_regulares": v["REGULARES"]}


def validar(valores):
    """{clave: número} -> los mismos, revisados. ValueError con el motivo."""
    limpio = {}
    for clave, nombre, _, minimo, maximo, _, unidad in PARAMETROS:
        if clave not in valores or valores[clave] is None:
            continue
        try:
            x = float(valores[clave])
        except (TypeError, ValueError):
            raise ValueError(f"{nombre}: tiene que ser un número")
        if not (minimo <= x <= maximo) or math.isnan(x):
            raise ValueError(f"{nombre}: entre {minimo:g} y {maximo:g}")
        if unidad == "sí/no" and x not in (0, 1):
            raise ValueError(f"{nombre}: sí (1) o no (0)")
        limpio[clave] = x
    desconocidas = set(valores) - CLAVES
    if desconocidas:
        raise ValueError(f"parámetros desconocidos: {sorted(desconocidas)}")
    v = {**por_defecto(), **limpio}
    if not v["viento_leves"] < v["viento_moderados"] < v["viento_regulares"]:
        raise ValueError("Viento: los topes tienen que ir de menor a mayor (leves < moderados < regulares)")
    return limpio


def leer(conn):
    """Los valores cambiados (los que no están, quedan por defecto)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('ajuste') IS NOT NULL")
        fila = cur.fetchone()
        if not (fila[0] if isinstance(fila, tuple) else list(fila.values())[0]):
            return {}
        cur.execute("SELECT clave, valor FROM ajuste WHERE clave LIKE 'verificacion.%'")
        filas = cur.fetchall()
    salida = {}
    for f in filas:
        clave, valor = (f[0], f[1]) if isinstance(f, tuple) else (f["clave"], f["valor"])
        clave = clave.removeprefix("verificacion.")
        if clave in CLAVES and isinstance(valor, (int, float)):
            salida[clave] = float(valor)
    return salida


def aplicar(cambiados):
    """Pone los valores en observado.py (los demás vuelven al defecto)."""
    v = {**por_defecto(), **cambiados}
    observado.UMBRAL_PP_MM = v["umbral_pp_mm"]
    observado.COMPLETITUD_MINIMA = v["completitud_minima"]
    observado.COMPLETITUD_VIENTO = v["completitud_viento"]
    observado.SUPERPOSICION_LLUVIA = v["superposicion_lluvia"]
    observado.TRAZA_CUENTA = bool(v["traza_cuenta"])
    observado.CATEGORIAS_VIENTO = (("LEVES", v["viento_leves"]), ("MODERADOS", v["viento_moderados"]),
                                   ("REGULARES", v["viento_regulares"]), ("FUERTES", math.inf))
    return v
