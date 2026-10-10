"""
Decodificador de mensajes SYNOP (FM 12, OMM) — lo esencial.

Un SYNOP es un mensaje en grupos de 5 cifras. Ejemplo real de La Plata:

  AAXX 10121 87593 32970 72106 10142 20118 30138 40220 52010 60001 78082 83530
       333 10187 20089 56999 82630=

Sección 0 (identificación)
  AAXX          tipo de mensaje (estación terrestre)
  YYGGi_w       día, hora UTC, indicador de unidades del viento
                (i_w = 0/1 -> m/s ; 3/4 -> nudos)
  IIiii         indicativo OMM de la estación (87593 = La Plata)

Sección 1 (datos principales)
  i_R i_x h VV  i_R: dónde va la lluvia; i_x: si hay tiempo presente;
                h: altura base de nubes; VV: visibilidad (código)
  N dd ff       nubosidad total (octas), dirección (decenas de °), velocidad
  1 s_n TTT     temperatura (décimas de °C; s_n=1 negativa)
  2 s_n TdTdTd  punto de rocío (si s_n=9: humedad relativa en %)
  3 PPPP        presión a nivel de estación (décimas de hPa, sin el millar)
  4 PPPP        presión a nivel del mar
  5 a ppp       tendencia de 3 h
  6 RRR t_R     precipitación RRR en el período t_R
  7 ww W1W2     tiempo presente y pasado
  8 Nh CL CM CH nubes

Sección 3 (empieza con "333", datos regionales)
  1 s_n TxTxTx  temperatura máxima
  2 s_n TnTnTn  temperatura mínima
  6 RRR t_R     precipitación (otro período)
  7 R24R24R24R24 precipitación de 24 h en décimas de mm

Referencia: OMM-N°306, Manual de Claves, Vol. I.1, FM 12 SYNOP.
"""
from datetime import datetime, timedelta, timezone

from .base import NUDOS_A_MS, Obs

# t_R -> horas del período de precipitación
PERIODO_LLUVIA = {"1": 6, "2": 12, "3": 18, "4": 24, "5": 1, "6": 2, "7": 3, "8": 9, "9": 15}


def _temp(grupo):
    """'1s_nTTT' -> °C. s_n: 0 positivo, 1 negativo."""
    signo, ttt = grupo[1], grupo[2:5]
    if "/" in ttt or signo not in "01":
        return None
    t = int(ttt) / 10
    return -t if signo == "1" else t


def _presion(pppp):
    """'0138' -> 1013.8 ; '9985' -> 998.5 (se omite el millar)."""
    if "/" in pppp:
        return None
    p = int(pppp) / 10
    return p + 1000 if p < 500 else p


def _lluvia(rrr):
    """Código RRR -> mm. 990 = inapreciable (traza), 991-999 = 0,1-0,9 mm."""
    if "/" in rrr:
        return None
    v = int(rrr)
    if v == 990:
        return 0.0  # traza: se guarda 0 (el mensaje crudo conserva el detalle)
    if v > 990:
        return (v - 990) / 10
    return float(v)


def _visibilidad(vv):
    """Código VV -> metros."""
    if "/" in vv:
        return None
    v = int(vv)
    if v <= 50:
        return v * 100.0
    if 56 <= v <= 80:
        return (v - 50) * 1000.0
    if 81 <= v <= 88:
        return ((v - 80) * 5 + 30) * 1000.0
    if v == 89:
        return 70000.0
    tabla = {90: 0, 91: 50, 92: 200, 93: 500, 94: 1000, 95: 2000, 96: 4000, 97: 10000, 98: 20000, 99: 50000}
    return float(tabla[v]) if v in tabla else None


def decodificar(mensaje, ts, estacion_id):
    """Devuelve la lista de Obs que se pueden extraer del mensaje.

    `ts`: instante de la observación (lo da Ogimet). Ante cualquier grupo raro
    se lo saltea: es preferible perder un dato que guardar uno mal leído.
    """
    texto = mensaje.replace("=", " ").split()
    if "NIL" in texto:
        return []
    try:
        i = texto.index("AAXX")
    except ValueError:
        return []
    grupos = texto[i + 1:]
    if len(grupos) < 3:
        return []
    yygg = grupos[0]  # grupos[1] es el indicativo OMM (lo da Ogimet aparte)
    iw = yygg[4] if len(yygg) == 5 else "1"
    factor_viento = NUDOS_A_MS if iw in "34" else 1.0

    obs = []

    def agregar(variable, valor):
        if valor is not None:
            obs.append(Obs(ts, estacion_id, variable, round(float(valor), 3)))

    def agregar_lluvia(grupo):
        horas = PERIODO_LLUVIA.get(grupo[4])
        mm = _lluvia(grupo[1:4])
        if horas in (1, 3, 6, 12, 24) and mm is not None:
            agregar(f"precip_{horas}h", mm)

    resto = grupos[2:]
    if len(resto) < 2:
        return []
    iihvv, nddff = resto[0], resto[1]
    if len(iihvv) == 5:
        agregar("visibilidad", _visibilidad(iihvv[3:5]))
    k = 2
    if len(nddff) == 5:
        n, dd, ff = nddff[0], nddff[1:3], nddff[3:5]
        if n.isdigit():
            agregar("nubosidad", int(n))
        if ff.isdigit():
            vel = int(ff)
            if vel == 99 and len(resto) > 2 and resto[2].startswith("00"):
                vel = int(resto[2][2:5])  # viento >= 99 unidades: grupo 00fff
                k = 3
            agregar("viento_vel", vel * factor_viento)
        if dd.isdigit() and int(dd) <= 36:
            agregar("viento_dir", int(dd) * 10)

    seccion = 1
    for g in resto[k:]:
        if g == "333":
            seccion = 3
            continue
        if g in ("555", "444", "222") or (len(g) == 5 and g.startswith("222")):
            seccion = 9  # secciones 2 (barcos), 4 y 5 (nacional): no se leen
            continue
        if len(g) != 5 or seccion == 9:
            continue
        c = g[0]
        if seccion == 1:
            if c == "1":
                agregar("temp", _temp(g))
            elif c == "2":
                if g[1] == "9" and g[2:].isdigit():
                    agregar("hum", int(g[2:]))
                else:
                    agregar("td", _temp(g))
            elif c == "3":
                agregar("pres_est", _presion(g[1:]))
            elif c == "4":
                # 4PPPP (presión a nivel del mar) empieza con 0 o 9 (1013,8 -> 40138);
                # si no, es 4a3hhh (geopotencial, estaciones de altura): se ignora.
                if g[1] in "09":
                    agregar("pnm", _presion(g[1:]))
            elif c == "6":
                agregar_lluvia(g)
            elif c == "7" and g[1:3].isdigit():
                agregar("ww", int(g[1:3]))
        elif seccion == 3:
            if c == "1":
                agregar("tmax", _temp(g))
            elif c == "2":
                agregar("tmin", _temp(g))
            elif c == "6":
                agregar_lluvia(g)
            elif c == "7" and "/" not in g[1:]:
                agregar("precip_24h", 0.0 if g[1:] == "9999" else int(g[1:]) / 10)
    return obs


def parsear_getsynop(texto):
    """Formato CSV de https://www.ogimet.com/cgi-bin/getsynop :
        87593,2026,10,10,12,00,AAXX 10121 87593 32970 ...=
    -> [(indicativo, ts_utc, mensaje)]"""
    salida = []
    for linea in texto.splitlines():
        partes = linea.strip().split(",", 6)
        if len(partes) != 7 or not partes[0].isdigit():
            continue
        try:
            ts = datetime(*(int(p) for p in partes[1:6]), tzinfo=timezone.utc)
        except ValueError:
            continue
        salida.append((partes[0], ts, partes[6].strip()))
    return salida


def ventanas(desde, hasta, dias=7):
    """Ogimet limita el tamaño de cada pedido: se parte en ventanas."""
    t = desde
    while t < hasta:
        fin = min(t + timedelta(days=dias), hasta)
        yield t, fin
        t = fin
