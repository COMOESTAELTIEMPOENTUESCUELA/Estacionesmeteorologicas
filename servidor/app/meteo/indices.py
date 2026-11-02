"""
Índices climáticos: el estado del ENSO, el SAM, la MJO, el Índico, el
Atlántico tropical y el Pacífico, para cualquier día o período.

Son series que publican NOAA (CPC, PSL, NCEI) y el BoM de Australia en
archivos de texto. La ingesta los baja dos veces por día (tarea "indices") y
los guarda, uno por índice, en:

    ARCHIVO_DIR/indices/<indice>.json   ->  {"indice", "bajado", "datos": [[fecha, valor, extra], ...]}

La web los lee de ahí (/api/cartas/indices). Si una fuente falla, queda el
último archivo bueno: los índices cambian despacio.

Uso a mano (en el servidor):

    docker compose exec ingesta python -m meteo.indices bajar
    docker compose exec ingesta python -m meteo.indices ver [--fecha 2026-10-28]

Cómo leer las fechas de cada índice:
- diario (SAM, MJO): el día.
- semanal (Niño 3.4 y 1+2 del CPC): el miércoles central de la semana.
- mensual (SOI, DMI, TNA, TSA, PDO, SAM mensual): el día 1 del mes.
- trimestral (ONI): el día 1 del mes CENTRAL del trimestre (ASO -> septiembre).
"""
import argparse
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)
CARPETA = "indices"
MESES = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
TRIMESTRES = {t: i + 1 for i, t in enumerate(("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
                                              "JJA", "JAS", "ASO", "SON", "OND", "NDJ"))}


# ---------------------------------------------------------------------------
# Lectores de cada formato (texto -> [(fecha, valor, extra)])
# ---------------------------------------------------------------------------
def leer_oni(texto):
    """CPC oni.ascii.txt:  "  ASO 2026  26.62  -0.71"  (trimestre, año, total, anomalía)."""
    salida = []
    for linea in texto.splitlines():
        p = linea.split()
        if len(p) == 4 and p[0] in TRIMESTRES and p[1].isdigit():
            try:
                salida.append((date(int(p[1]), TRIMESTRES[p[0]], 1), float(p[3]), {"trimestre": p[0]}))
            except ValueError:
                continue
    return salida


NUMERO = re.compile(r"-?\d+\.\d+")


def leer_nino_semanal(texto, region):
    """CPC wksst9120.for: " 01OCT2026     20.1-0.4     24.9-0.3     26.4-0.4     28.6 0.0"
    (Niño 1+2, 3, 3.4 y 4: temperatura y anomalía pegadas). region: 0=1+2, 1=3, 2=3.4, 3=4."""
    salida = []
    for linea in texto.splitlines():
        m = re.match(r"\s*(\d{2})([A-Z]{3})(\d{4})(.*)", linea)
        if not m or m.group(2) not in MESES:
            continue
        nums = NUMERO.findall(m.group(4))
        if len(nums) < 8:
            continue
        try:
            f = date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
        except ValueError:
            continue
        salida.append((f, float(nums[2 * region + 1]), {"sst": float(nums[2 * region])}))
    return salida


def leer_diario(texto):
    """año, mes, día, valor (con comas o espacios). Ej. el SAM diario del CPC."""
    salida = []
    for linea in texto.splitlines():
        m = re.match(r"\s*(\d{4})[,\s]+(\d{1,2})[,\s]+(\d{1,2})[,\s]+(-?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*$", linea)
        if not m:
            continue
        v = float(m.group(4))
        if abs(v) >= 99:
            continue
        try:
            salida.append((date(int(m.group(1)), int(m.group(2)), int(m.group(3))), v, None))
        except ValueError:
            continue
    return salida


def leer_mensual_columna(texto):
    """año, mes, valor en una línea: el SAM mensual del CPC (" 2026   9   0.512")."""
    salida = []
    for linea in texto.splitlines():
        m = re.match(r"\s*(\d{4})\s+(\d{1,2})\s+(-?\d*\.?\d+)\s*$", linea)
        if m and 1 <= int(m.group(2)) <= 12 and abs(float(m.group(3))) < 99:
            salida.append((date(int(m.group(1)), int(m.group(2)), 1), float(m.group(3)), None))
    return salida


def leer_tabla_mensual(texto):
    """Formato de PSL y NCEI: una fila por año con los 12 meses
    ("2026  0.31  0.12 ... -99.99"). Los faltantes son -99.99, -9999, 99.99..."""
    salida = []
    for linea in texto.splitlines():
        p = linea.split()
        if len(p) != 13 or not re.fullmatch(r"\d{4}", p[0]) or not 1850 <= int(p[0]) <= 2100:
            continue
        for mes, s in enumerate(p[1:], start=1):
            try:
                v = float(s)
            except ValueError:
                continue
            if abs(v) >= 99:
                continue
            salida.append((date(int(p[0]), mes, 1), v, None))
    return salida


def leer_rmm(texto):
    """BoM rmm.74toRealtime.txt: "  2026  10  28   0.81  -1.02  4   1.30  Prelim_value..."
    (año, mes, día, RMM1, RMM2, fase, amplitud). El valor guardado es la amplitud."""
    salida = []
    for linea in texto.splitlines():
        p = linea.split()
        if len(p) < 7 or not re.fullmatch(r"\d{4}", p[0]):
            continue
        try:
            r1, r2, fase, amp = float(p[3]), float(p[4]), int(p[5]), float(p[6])
            f = date(int(p[0]), int(p[1]), int(p[2]))
        except ValueError:
            continue
        if not 1 <= fase <= 8 or abs(amp) > 50 or abs(r1) > 50:
            continue
        salida.append((f, round(amp, 2), {"fase": fase, "rmm1": round(r1, 2), "rmm2": round(r2, 2)}))
    return salida


# ---------------------------------------------------------------------------
# Los índices: de dónde salen y cómo se interpretan
# ---------------------------------------------------------------------------
# umbral: desde qué valor (en valor absoluto) se considera una fase.
# fases: (texto si es positivo, texto si es negativo, texto si es neutral).
INDICES = {
    "oni": dict(nombre="ENSO (ONI)", grupo="ENSO", unidad="°C", frecuencia="trimestral", umbral=0.5, vigencia=130,
                url="https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", leer=leer_oni,
                fases=("El Niño", "La Niña", "Neutral"),
                que="Anomalía de la temperatura del mar en la región Niño 3.4 (Pacífico ecuatorial central), promedio de 3 meses. "
                    "Es el índice oficial de NOAA: hay episodio Niño/Niña si pasa ±0,5 °C durante 5 trimestres seguidos."),
    "nino34": dict(nombre="Niño 3.4 semanal", grupo="ENSO", unidad="°C", frecuencia="semanal", umbral=0.5, vigencia=14,
                   url="https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for", leer=lambda t: leer_nino_semanal(t, 2),
                   fases=("cálido", "frío", "neutral"),
                   que="La misma región que el ONI, pero de una semana: muestra hacia dónde va el ENSO antes que el promedio de 3 meses."),
    "nino12": dict(nombre="Niño 1+2 semanal", grupo="ENSO", unidad="°C", frecuencia="semanal", umbral=0.5, vigencia=14,
                   url="https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for", leer=lambda t: leer_nino_semanal(t, 0),
                   fases=("cálido", "frío", "neutral"),
                   que="Pacífico frente a Perú y Ecuador ('Niño costero'). Puede estar caliente aunque el Pacífico central no."),
    "soi": dict(nombre="SOI (Oscilación del Sur)", grupo="ENSO", unidad="", frecuencia="mensual", umbral=1.0, vigencia=62,
                url="https://psl.noaa.gov/data/correlation/soi.data", leer=leer_tabla_mensual,
                fases=("positivo (como La Niña)", "negativo (como El Niño)", "neutral"),
                que="Diferencia de presión entre Tahití y Darwin: la parte atmosférica del ENSO. Negativo y sostenido acompaña a El Niño."),
    "sam": dict(nombre="SAM (Modo Anular del Sur)", grupo="SAM", unidad="", frecuencia="diario", umbral=0.5, vigencia=7,
                url="https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.aao.cdas.z700.19790101_current.csv", leer=leer_diario,
                fases=("positivo", "negativo", "neutral"),
                que="También llamado AAO. Positivo: los oestes y las tormentas se corren hacia la Antártida; negativo: se acercan a "
                    "Sudamérica. Cambia en días a semanas.",
                efecto="El SAM positivo en primavera se asocia a menos lluvia en el sudeste de Sudamérica; el negativo, a más "
                       "pasajes de frentes por la región."),
    "sam_mensual": dict(nombre="SAM mensual", grupo="SAM", unidad="", frecuencia="mensual", umbral=0.5, vigencia=62,
                        url="https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/aao/monthly.aao.index.b79.current.ascii",
                        leer=leer_mensual_columna, fases=("positivo", "negativo", "neutral"),
                        que="El SAM promediado en el mes."),
    "mjo": dict(nombre="MJO (Madden-Julian)", grupo="MJO", unidad="", frecuencia="diario", umbral=1.0, vigencia=7,
                url="http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt", leer=leer_rmm,
                fases=("activa", "activa", "débil"),
                que="Un pulso de nubes y lluvia que recorre el trópico de oeste a este en 30 a 60 días. Fase (1 a 8): dónde está la "
                    "convección; amplitud: qué tan fuerte (menos de 1, débil).",
                efecto="Modula la lluvia y la temperatura en Sudamérica en escala de 1 a 4 semanas; el efecto depende de la fase y "
                       "la estación del año."),
    "dmi": dict(nombre="Dipolo del Índico (DMI)", grupo="Océanos", unidad="°C", frecuencia="mensual", umbral=0.4, vigencia=95,
                url="https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data", leer=leer_tabla_mensual,
                fases=("positivo", "negativo", "neutral"),
                que="Diferencia de temperatura del mar entre el oeste y el este del océano Índico tropical.",
                efecto="El dipolo positivo en primavera se asocia a más lluvia en el sudeste de Sudamérica (por ondas que viajan "
                       "por la atmósfera hasta acá)."),
    "tna": dict(nombre="Atlántico tropical norte (TNA)", grupo="Océanos", unidad="°C", frecuencia="mensual", umbral=0.3, vigencia=95,
                url="https://psl.noaa.gov/data/correlation/tna.data", leer=leer_tabla_mensual,
                fases=("cálido", "frío", "neutral"),
                que="Anomalía de la temperatura del mar en el Atlántico tropical norte."),
    "tsa": dict(nombre="Atlántico tropical sur (TSA)", grupo="Océanos", unidad="°C", frecuencia="mensual", umbral=0.3, vigencia=95,
                url="https://psl.noaa.gov/data/correlation/tsa.data", leer=leer_tabla_mensual,
                fases=("cálido", "frío", "neutral"),
                que="Anomalía de la temperatura del mar en el Atlántico tropical sur (frente a Brasil y Angola)."),
    "dipolo_atl": dict(nombre="Dipolo del Atlántico tropical", grupo="Océanos", unidad="°C", frecuencia="mensual", umbral=0.3,
                       vigencia=95, url=None, de=("tna", "tsa"),
                       fases=("positivo (norte más cálido)", "negativo (sur más cálido)", "neutral"),
                       que="TNA menos TSA: el gradiente de temperatura del mar a través del ecuador en el Atlántico. Corre la "
                           "zona de convergencia intertropical hacia el lado más cálido."),
    "pdo": dict(nombre="PDO (Pacífico decadal)", grupo="Océanos", unidad="", frecuencia="mensual", umbral=0.5, vigencia=95,
                url="https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat", leer=leer_tabla_mensual,
                fases=("positiva (cálida)", "negativa (fría)", "neutral"),
                que="Patrón de la temperatura del mar del Pacífico norte que cambia en décadas. Puede reforzar o atenuar al ENSO."),
}
# Para consultar: de dónde sale cada índice, qué es, y de dónde sale el
# umbral que usamos para decir "en fase". Los umbrales marcados "criterio de
# esta página" NO son oficiales: son una convención razonable para la
# franja de colores (se pueden cambiar arriba, en "umbral").
PSL_LISTA = ("NOAA PSL: lista de índices climáticos (definiciones)", "https://psl.noaa.gov/data/climateindices/list/")
CONSULTAR = {
    "oni": dict(umbral="oficial de NOAA (±0,5 °C; episodio con 5 trimestres seguidos)", links=[
        ("NOAA CPC: tabla del ONI y criterio de episodios", "https://origin.cpc.ncep.noaa.gov/products/analysis_monitoring/ensostuff/ONI_v5.php"),
        ("NOAA CPC: discusión mensual del ENSO", "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml")]),
    "nino34": dict(umbral="el mismo del ONI (±0,5 °C), aplicado a la semana", links=[
        ("NOAA CPC: índices de temperatura del mar (Niño 1+2, 3, 3.4, 4)", "https://www.cpc.ncep.noaa.gov/data/indices/"),
        ("NOAA CPC: discusión mensual del ENSO", "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml")]),
    "nino12": dict(umbral="criterio de esta página (±0,5 °C, como el Niño 3.4)", links=[
        ("NOAA CPC: índices de temperatura del mar", "https://www.cpc.ncep.noaa.gov/data/indices/")]),
    "soi": dict(umbral="criterio de esta página (±1 desvío)", links=[
        PSL_LISTA, ("NOAA CPC: SOI", "https://www.cpc.ncep.noaa.gov/data/indices/soi")]),
    "sam": dict(umbral="criterio de esta página (±0,5 desvío)", links=[
        ("NOAA CPC: índice AAO / SAM (diario y mensual)", "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/aao/aao.shtml"),
        ("Silvestri y Vera (2003), SAM y lluvia en el sudeste de Sudamérica (GRL)", "https://doi.org/10.1029/2003GL018277")]),
    "sam_mensual": dict(umbral="criterio de esta página (±0,5 desvío)", links=[
        ("NOAA CPC: índice AAO / SAM", "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/aao/aao.shtml")]),
    "mjo": dict(umbral="habitual en la literatura: amplitud 1 (Wheeler y Hendon, 2004)", links=[
        ("BoM (Australia): monitoreo de la MJO y datos RMM", "http://www.bom.gov.au/climate/mjo/"),
        ("NOAA CPC: MJO, diagnóstico y pronóstico", "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/mjo.shtml"),
        ("Wheeler y Hendon (2004), el índice RMM (Monthly Weather Review)",
         "https://doi.org/10.1175/1520-0493(2004)132<1917:AARMMI>2.0.CO;2")]),
    "dmi": dict(umbral="el que usa el BoM (±0,4 °C)", links=[
        ("NOAA PSL: serie del DMI", "https://psl.noaa.gov/gcos_wgsp/Timeseries/DMI/"),
        ("BoM (Australia): ENSO y dipolo del Índico", "http://www.bom.gov.au/climate/enso/"),
        ("Chan, Behera y Yamagata (2008), el dipolo del Índico y la lluvia en Sudamérica (GRL)", "https://doi.org/10.1029/2008GL034204")]),
    "tna": dict(umbral="criterio de esta página (±0,3 °C)", links=[PSL_LISTA]),
    "tsa": dict(umbral="criterio de esta página (±0,3 °C)", links=[PSL_LISTA]),
    "dipolo_atl": dict(umbral="criterio de esta página (±0,3 °C)", links=[
        PSL_LISTA, ("(es la resta TNA − TSA, calculada en este servidor con las dos series de PSL)", None)]),
    "pdo": dict(umbral="criterio de esta página (±0,5)", links=[
        ("NOAA NCEI: PDO (definición y datos)", "https://www.ncei.noaa.gov/access/monitoring/pdo/")]),
}

REGION_MJO = {1: "hemisferio occidental y África", 2: "océano Índico", 3: "océano Índico", 4: "continente marítimo",
              5: "continente marítimo", 6: "Pacífico oeste", 7: "Pacífico oeste", 8: "hemisferio occidental y África"}


# ---------------------------------------------------------------------------
# Bajar y guardar
# ---------------------------------------------------------------------------
def ruta(raiz, indice):
    return Path(raiz) / CARPETA / f"{indice}.json"


def guardar(raiz, indice, datos, ahora=None):
    destino = ruta(raiz, indice)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps({"indice": indice, "bajado": (ahora or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
                               "datos": [[f.isoformat(), v, x] if x else [f.isoformat(), v] for f, v, x in sorted(datos, key=lambda d: d[0])]},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(destino)


_cache = {}


def leer(raiz, indice):
    """[(date, valor, extra)] (en caché mientras el archivo no cambie)."""
    r = ruta(raiz, indice)
    try:
        mt = r.stat().st_mtime
    except FileNotFoundError:
        return [], None
    c = _cache.get(r)
    if c and c[0] == mt:
        return c[1], c[2]
    d = json.loads(r.read_text(encoding="utf-8"))
    datos = [(date.fromisoformat(x[0]), x[1], x[2] if len(x) > 2 else None) for x in d["datos"]]
    _cache[r] = (mt, datos, d.get("bajado"))
    return datos, d.get("bajado")


def bajar(raiz, sesion, solo=None):
    """Baja todos los índices (o los de `solo`). Devuelve {indice: resumen}."""
    resumen, textos = {}, {}
    for k, ind in INDICES.items():
        if solo and k not in solo or not ind.get("url"):
            continue
        try:
            if ind["url"] not in textos:
                r = sesion.get(ind["url"], timeout=60)
                r.raise_for_status()
                textos[ind["url"]] = r.text
            datos = ind["leer"](textos[ind["url"]])
            if not datos:
                raise ValueError("el archivo no tiene datos con el formato esperado")
            guardar(raiz, k, datos)
            resumen[k] = f"{len(datos)} datos, el último del {datos[-1][0]}: {datos[-1][1]}"
        except Exception as ex:          # una fuente caída no frena a las demás
            resumen[k] = f"FALLÓ ({str(ex)[:150]}); queda el archivo anterior"
            log.warning("índice %s: %s", k, resumen[k])
    # Los que se calculan con otros (el dipolo del Atlántico: TNA - TSA).
    for k, ind in INDICES.items():
        if not ind.get("de") or (solo and k not in solo):
            continue
        a, b = (dict((f, v) for f, v, _ in leer(raiz, x)[0]) for x in ind["de"])
        datos = [(f, round(a[f] - b[f], 2), None) for f in sorted(set(a) & set(b))]
        if datos:
            guardar(raiz, k, datos)
            resumen[k] = f"{len(datos)} datos, el último del {datos[-1][0]}: {datos[-1][1]}"
    return resumen


# ---------------------------------------------------------------------------
# Interpretar: la fase en una fecha y el resumen de un período
# ---------------------------------------------------------------------------
def fase(indice, valor, extra=None):
    """(texto, signo) con signo "+", "-" o "0"."""
    ind = INDICES[indice]
    if indice == "mjo":
        if valor < ind["umbral"]:
            return "débil (sin señal clara)", "0"
        f = (extra or {}).get("fase")
        return f"fase {f} ({REGION_MJO.get(f, '')})", "+"
    pos, neg, neu = ind["fases"]
    u = ind["umbral"]
    if valor >= u:
        texto, signo = pos, "+"
    elif valor <= -u:
        texto, signo = neg, "-"
    else:
        return neu, "0"
    if indice in ("oni", "nino34"):     # intensidad del ENSO (umbrales de NOAA)
        a = abs(valor)
        moderado = "moderada" if texto == "La Niña" else "moderado"
        texto += " " + ("débil" if a < 1 else moderado if a < 1.5 else "fuerte" if a < 2 else "muy fuerte")
    return texto, signo


def episodio_enso(datos, i):
    """¿El trimestre i forma parte de un episodio oficial (5 trimestres
    seguidos del mismo lado de ±0,5)? -> "El Niño", "La Niña" o None."""
    v = datos[i][1]
    if abs(v) < 0.5:
        return None
    signo = 1 if v > 0 else -1
    j0 = j1 = i
    while j0 > 0 and datos[j0 - 1][1] * signo >= 0.5:
        j0 -= 1
    while j1 < len(datos) - 1 and datos[j1 + 1][1] * signo >= 0.5:
        j1 += 1
    return ("El Niño" if signo > 0 else "La Niña") if j1 - j0 + 1 >= 5 else None


def _condicion(texto):
    """"El Niño débil" -> "condición Niño débil" (cuando no es un episodio oficial)."""
    return texto.replace("El Niño", "condición Niño").replace("La Niña", "condición Niña")


def _ultimo(datos, f):
    """Índice del último dato con fecha <= f (o -1)."""
    lo, hi = 0, len(datos)
    while lo < hi:
        m = (lo + hi) // 2
        if datos[m][0] <= f:
            lo = m + 1
        else:
            hi = m
    return lo - 1


def estado(raiz, fecha, desde=None, hasta=None, serie_desde=None, solo=None):
    """Para cada índice: el valor vigente en `fecha`, el resumen del período
    desde-hasta (si se pide) y la serie desde `serie_desde` hasta `fecha`."""
    salida = []
    serie_desde = serie_desde or fecha - timedelta(days=365)
    for k, ind in INDICES.items():
        if solo and k != solo:
            continue
        datos, bajado = leer(raiz, k)
        r = {"id": k, "nombre": ind["nombre"], "grupo": ind["grupo"], "unidad": ind["unidad"], "frecuencia": ind["frecuencia"],
             "umbral": ind["umbral"], "fases": ind["fases"], "que": ind["que"], "efecto": ind.get("efecto"), "fuente": ind.get("url"), "bajado": bajado,
             "umbral_origen": CONSULTAR[k]["umbral"], "links": [{"texto": t, "url": u} for t, u in CONSULTAR[k]["links"]],
             "valor": None, "periodo": None, "serie": []}
        if not datos:
            salida.append(r)
            continue
        i = _ultimo(datos, fecha)
        if i >= 0:
            f, v, x = datos[i]
            texto, signo = fase(k, v, x)
            r["valor"] = {"fecha": f.isoformat(), "valor": v, "extra": x, "fase": texto, "signo": signo,
                          "vigente": (fecha - f).days <= ind["vigencia"]}
            if k == "oni":
                ep = episodio_enso(datos, i)
                r["valor"]["episodio"] = ep
                if ep is None and signo != "0":
                    r["valor"]["fase"] = _condicion(texto)
                    r["valor"]["nota"] = "Todavía no es un episodio oficial: hacen falta 5 trimestres seguidos pasando ±0,5 °C."
        if desde and hasta:
            # Los mensuales cuentan el mes en que empieza el período.
            d0 = desde.replace(day=1) if ind["frecuencia"] in ("mensual", "trimestral") else desde
            en = [(f, v, x) for f, v, x in datos if d0 <= f <= hasta]
            if en:
                media = round(sum(v for _, v, _ in en) / len(en), 2)
                p = {"n": len(en), "media": media, "min": min(v for _, v, _ in en), "max": max(v for _, v, _ in en)}
                if k == "mjo":
                    activos = [x["fase"] for _, v, x in en if v >= 1]
                    cuenta = {f: activos.count(f) for f in range(1, 9) if activos.count(f)}
                    p["dias_activa"] = len(activos)
                    p["fases"] = cuenta
                    p["fase"] = (f"activa {round(100 * len(activos) / len(en))} % de los días; más frecuente: fase "
                                 f"{max(cuenta, key=cuenta.get)}" if cuenta else "débil todo el período")
                else:
                    t = fase(k, media)[0]
                    p["fase"] = (_condicion(t) if k == "oni" else t) + " en promedio"
                r["periodo"] = p
        r["serie"] = [[f.isoformat(), v] + ([x] if k == "mjo" else []) for f, v, x in datos[max(0, _ultimo(datos, serie_desde)):i + 1]]
        salida.append(r)
    return salida


# ---------------------------------------------------------------------------
def main(argv=None):
    from .ingesta.base import sesion_http
    ap = argparse.ArgumentParser(prog="python -m meteo.indices", description="Índices climáticos (ENSO, SAM, MJO...)")
    ap.add_argument("comando", choices=["bajar", "ver"])
    ap.add_argument("--fecha", type=date.fromisoformat, default=date.today())
    ap.add_argument("--solo", help="índices separados por coma (ej. oni,sam)")
    a = ap.parse_args(argv)
    raiz = os.environ.get("ARCHIVO_DIR", "/archivo")
    if a.comando == "bajar":
        logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
        for k, s in bajar(raiz, sesion_http(), set(a.solo.split(",")) if a.solo else None).items():
            print(f"{k:12} {s}")
        return
    for r in estado(raiz, a.fecha):
        v = r["valor"]
        print(f"{r['nombre']:32} " + (f"{v['valor']:+.2f} ({v['fecha']}) {v['fase']}{'' if v['vigente'] else '  [dato viejo]'}"
                                       if v else "sin datos (correr: python -m meteo.indices bajar)"))


if __name__ == "__main__":
    main()
