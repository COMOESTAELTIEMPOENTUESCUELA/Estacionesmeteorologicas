"""
Lo que se PRONOSTICÓ, llevado a las mismas categorías que lo observado.

Se usan los mismos criterios que la hoja Verificacion_Pronostico del equipo
(función deriveDayVerification del Apps Script de Pronostico-unlp), para que
los dos sistemas cuenten lo mismo:
  - día con precipitación: algún período tiene descripción de precipitación
    (lluvias, lloviznas, chaparrones, tormentas...), sin importar la
    probabilidad;
  - dirección: todas las direcciones nombradas en los períodos del día;
  - intensidad: la mayor de las nombradas ("leves a moderados" -> MODERADOS).
"""
import re

DIRECCIONES = {
    "norte": "N", "noreste": "NE", "este": "E", "sudeste": "SE", "sureste": "SE",
    "sur": "S", "sudoeste": "SW", "suroeste": "SW", "oeste": "W", "noroeste": "NW",
}
INTENSIDADES = ("LEVES", "MODERADOS", "REGULARES", "FUERTES")


def direcciones(texto):
    """'sudeste rotando al sur' -> {'SE', 'S'}"""
    palabras = re.findall(r"[a-záéíóú]+", (texto or "").lower())
    return {DIRECCIONES[p] for p in palabras if p in DIRECCIONES}


def intensidad(texto):
    """Mayor categoría nombrada en el texto, o None."""
    t = (texto or "").upper()
    nombradas = [i for i, nombre in enumerate(INTENSIDADES) if nombre[:-1] in t]  # LEVE/LEVES, FUERTE/FUERTES
    return INTENSIDADES[max(nombradas)] if nombradas else None


def derivar(periodos):
    periodos = periodos or []
    descripciones = [p.get("precipitation_description") for p in periodos if p.get("precipitation_description")]
    dirs = set()
    for p in periodos:
        dirs |= direcciones(p.get("wind_direction"))
    ints = [intensidad(p.get("wind_intensity")) for p in periodos]
    ints = [i for i in ints if i]
    con_temp = [p for p in periodos if p.get("temperature") is not None]
    min_periodo = min(con_temp, key=lambda p: p["temperature"])["period"] if con_temp else None
    return {
        "pp": bool(descripciones),
        "pp_tipo": ", ".join(dict.fromkeys(descripciones)),
        "viento_dirs": sorted(dirs, key=["N", "NE", "E", "SE", "S", "SW", "W", "NW"].index),
        "viento_int": max(ints, key=INTENSIDADES.index) if ints else None,
        "tormenta": any(re.search("tormenta", d, re.I) for d in descripciones),
        "tmin_periodo": min_periodo,
    }
