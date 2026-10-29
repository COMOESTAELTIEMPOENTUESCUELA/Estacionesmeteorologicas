"""
Página de cartas de Alicia Bentley (análisis y pronósticos del GFS):

    https://www.atmos.albany.edu/student/abentley/realtime/standard.php?domain=D&variable=V
    imágenes:  .../realtime/images/D/V/V_N.png       (N = número de cuadro)

Cómo se sabe la fecha de cada cuadro (de functions_gfs.js de la página):

    hora válida = corrida + (N - startFrame) * 6 h

La corrida ("String(2026101206") y startFrame (57) están en el HTML. Los
cuadros 1 a startFrame son ANÁLISIS (los 14 días anteriores y el de la
corrida); los siguientes, pronósticos (~8 días). Los análisis se archivan;
del pronóstico se guarda solo la última corrida (ver pronostico.py).

Cuidado: cuando sale una corrida nueva, TODOS los cuadros se renumeran. Si
se baja justo mientras la página se actualiza, una imagen todavía de la
corrida anterior quedaría con la fecha corrida 6 h. Por eso se exige que la
imagen haya sido modificada después del inicio de la corrida.
"""
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

BASE = "https://www.atmos.albany.edu/student/abentley/realtime/"


def leer_corrida(html):
    """HTML de standard.php -> {"inicio": datetime UTC, "start", "min", "max"}."""
    def entero(nombre):
        m = re.search(rf"var\s+{nombre}\s*=\s*(\d+)", html)
        if not m:
            raise ValueError(f"no se encontró {nombre} en la página de Bentley")
        return int(m.group(1))
    m = re.search(r"String\((\d{10})", html)
    if not m:
        raise ValueError("no se encontró la fecha de la corrida en la página de Bentley")
    inicio = datetime.strptime(m.group(1), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return {"inicio": inicio, "start": entero("startFrame"), "min": entero("minFrame"), "max": entero("maxFrame")}


def cuadros_analisis(corrida, horas_utc=(0, 6, 12, 18)):
    """[(número de cuadro, hora válida)] de los análisis, del más nuevo al más viejo."""
    salida = []
    for n in range(corrida["start"], corrida["min"] - 1, -1):
        valida = corrida["inicio"] + timedelta(hours=6 * (n - corrida["start"]))
        if valida.hour in horas_utc:
            salida.append((n, valida))
    return salida


def cuadros_pronostico(corrida):
    """[(número de cuadro, hora válida, plazo en horas)] de los PRONÓSTICOS de
    la corrida: los cuadros después de startFrame, hasta maxFrame (~8 días)."""
    salida = []
    for n in range(corrida["start"] + 1, corrida["max"] + 1):
        plazo = 6 * (n - corrida["start"])
        salida.append((n, corrida["inicio"] + timedelta(hours=plazo), plazo))
    return salida


def url_pagina(dominio, variable):
    return f"{BASE}standard.php?domain={dominio}&variable={variable}"


def url_imagen(dominio, variable, n):
    return f"{BASE}images/{dominio}/{variable}/{variable}_{n}.png"


def es_de_esta_corrida(cabeceras, inicio, sin_fecha=True):
    """Last-Modified de la imagen >= inicio de la corrida (las imágenes se
    generan horas después del inicio). Sin esa cabecera (o ilegible) no se
    puede comprobar: devuelve sin_fecha (los pronósticos piden False: un
    cuadro que no existe puede venir como una imagen vieja cualquiera)."""
    lm = cabeceras.get("Last-Modified")
    if not lm:
        return sin_fecha
    try:
        return parsedate_to_datetime(lm) >= inicio
    except (TypeError, ValueError):
        return sin_fecha
