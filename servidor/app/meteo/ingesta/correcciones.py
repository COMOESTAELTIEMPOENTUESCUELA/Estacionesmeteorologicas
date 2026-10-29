"""
Correcciones a mano de pronósticos: publicados con la fecha equivocada, o
con un valor mal tipeado (ver CAMPOS y corregir_valores, más abajo).

Caso típico: el pronóstico del día publicado a la mañana con la casilla
"CREAR PARA MAÑANA (Turno noche)" tildada. Queda guardado como del día
siguiente, con TODAS las fechas corridas un día, y la verificación lo
compara con lo que pasó al otro día.

Las correcciones se anotan en config/correcciones_pronosticos.yaml (es el
registro de qué se tocó y por qué) y se aplican:
  - con "python -m meteo.ingesta aplicar-correcciones", a lo ya guardado;
  - solas después de cada captura de pronósticos, por si la publicación
    equivocada llega más tarde.

La emisión se corrige EN EL LUGAR: cambian las fechas, se agrega
"correccion_manual" al contenido y la huella queda la del original. Así,
si el Apps Script vuelve a mandar la versión equivocada, el servidor la
reconoce como ya guardada y no la duplica.
"""
import logging
import os
from datetime import date, timedelta

import yaml
from psycopg.types.json import Jsonb

from .base import num
from .fuentes.hoja_verificacion import ORIGEN as RESUMIDA
from .fuentes.pronosticos import correr_fechas

log = logging.getLogger(__name__)
RUTA = os.environ.get("CORRECCIONES_CONFIG", "/config/correcciones_pronosticos.yaml")


def cargar(ruta=None):
    ruta = ruta or RUTA
    try:
        with open(ruta, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("correcciones") or []
    except FileNotFoundError:
        return []


# Valores que se pueden corregir: nombre en el YAML (y en pronostico_dia) ->
# nombre en el JSON publicado (daily_forecasts).
CAMPOS = {"temp_min": "temp_min", "temp_max": "temp_max",
          "temp_min_suburbana": "temp_min_suburban", "temp_max_suburbana": "temp_max_suburban"}


def corregir_valores(cur, c):
    """Un valor mal tipeado (ej. mínima 14 en vez de 4). En el YAML:
        {ciudad, fecha_guardada, publicado_por, dia, valores: {temp_min: 4}, motivo}
    dia: el día pronosticado que tiene el error. Se cambia EN EL LUGAR (en el
    JSON y en pronostico_dia) y se anota en "correccion_valores" qué había
    antes. La huella no cambia: si el Apps Script vuelve a mandar la versión
    equivocada, se reconoce como ya guardada."""
    fecha = date.fromisoformat(str(c["fecha_guardada"]))
    dia = str(c["dia"])
    etiqueta = f"{c['ciudad']} {fecha} ({c['publicado_por']}), día {dia}"
    raros = set(c["valores"]) - set(CAMPOS)
    if raros:
        return f"{etiqueta}: no sé corregir {sorted(raros)} (solo {sorted(CAMPOS)})"
    cur.execute("""SELECT id, contenido FROM pronostico_emision
                   WHERE ciudad = %s AND fecha_pronostico = %s AND publicado_por = %s
                     AND (%s::text IS NULL OR huella = %s::text)""",
                (c["ciudad"], fecha, c["publicado_por"], c.get("huella"), c.get("huella")))
    filas = cur.fetchall()
    if not filas:
        return f"{etiqueta}: no está en el servidor"
    cambiadas = 0
    for id_, contenido in filas:
        dias = contenido.get("daily_forecasts") or []
        d = next((x for x in dias if str(x.get("date")) == dia), None)
        if d is None:
            continue
        antes = {k: d.get(CAMPOS[k]) for k in c["valores"]}
        if all(num(antes[k]) == num(v) for k, v in c["valores"].items()):   # "4" y 4 son lo mismo
            continue                                  # ya estaba corregido
        for k, v in c["valores"].items():
            d[CAMPOS[k]] = v
        contenido.setdefault("correccion_valores", []).append(
            {"dia": dia, "antes": antes, "despues": c["valores"], "motivo": c.get("motivo", "")})
        cur.execute("UPDATE pronostico_emision SET contenido = %s WHERE id = %s", (Jsonb(contenido), id_))
        for k, v in c["valores"].items():
            # k sale de CAMPOS (lista fija): se puede poner en el SQL.
            cur.execute(f"UPDATE pronostico_dia SET {k} = %s WHERE emision_id = %s AND fecha = %s", (v, id_, dia))
        cambiadas += 1
    if not cambiadas:
        return f"{etiqueta}: ya estaba corregido"
    return f"{etiqueta}: corregido {c['valores']}"


def aplicar(conn, correcciones):
    """Aplica las que todavía no se aplicaron. Devuelve una línea por corrección."""
    salida = []
    with conn.cursor() as cur:
        for c in correcciones:
            if "valores" in c:
                salida.append(corregir_valores(cur, c))
                continue
            fecha = date.fromisoformat(str(c["fecha_guardada"]))
            dias = int(c["mover_dias"])
            # La hora escrita en el formulario ayuda a no confundirla con
            # otra emisión de la misma persona; las importadas de la hoja no la tienen.
            cur.execute("""
                SELECT id, contenido FROM pronostico_emision
                WHERE ciudad = %s AND fecha_pronostico = %s AND publicado_por = %s
                  AND NOT contenido ? 'correccion_manual'
                  AND (hora_emision IS NULL OR %s::text IS NULL OR hora_emision = %s::text)
                  AND (%s::text IS NULL OR huella = %s::text)""",
                        (c["ciudad"], fecha, c["publicado_por"], c.get("hora_emision"), c.get("hora_emision"),
                         c.get("huella"), c.get("huella")))
            filas = cur.fetchall()
            etiqueta = f"{c['ciudad']} {fecha} ({c['publicado_por']})"
            # ¿Ya hay una versión COMPLETA corregida? (la resumida de la hoja de
            # verificación no cuenta: si llega la completa, se corrige y gana).
            cur.execute("""SELECT 1 FROM pronostico_emision WHERE ciudad = %s AND publicado_por = %s
                           AND contenido -> 'correccion_manual' ->> 'fecha_original' = %s
                           AND contenido ->> 'origen' IS DISTINCT FROM %s""",
                        (c["ciudad"], c["publicado_por"], fecha.isoformat(), RESUMIDA))
            ya_corregida = cur.fetchone() is not None
            if not filas:
                cur.execute("""SELECT 1 FROM pronostico_emision WHERE ciudad = %s AND publicado_por = %s
                               AND contenido -> 'correccion_manual' ->> 'fecha_original' = %s""",
                            (c["ciudad"], c["publicado_por"], fecha.isoformat()))
                salida.append(f"{etiqueta}: {'ya estaba corregido' if cur.fetchone() else 'no está en el servidor'}")
                continue
            if ya_corregida:
                # Ya está la versión corregida (ej. cargada a mano con importar-emision):
                # esta es la misma publicación con la fecha mal, entró de nuevo
                # (ej. al importar la hoja). Se descarta para no tenerla dos veces.
                cur.execute("DELETE FROM pronostico_emision WHERE id = ANY(%s)", ([f[0] for f in filas],))
                salida.append(f"{etiqueta}: ya estaba corregido; se descartó la copia con la fecha mal")
                continue
            for fila in filas:
                id_, contenido = fila
                nuevo = correr_fechas(contenido, dias, str(c["publicado_el"]), c["motivo"])
                cur.execute("UPDATE pronostico_emision SET fecha_pronostico = %s, contenido = %s WHERE id = %s",
                            (fecha + timedelta(days=dias), Jsonb(nuevo), id_))
                # En dos pasos: corriendo de a un día, el 10 pasaría a 9 antes de que
                # el 9 se mueva y chocarían (fecha es parte de la clave).
                cur.execute("UPDATE pronostico_dia SET fecha = fecha + %s WHERE emision_id = %s", (100000, id_))
                cur.execute("UPDATE pronostico_dia SET fecha = fecha - %s WHERE emision_id = %s", (100000 - dias, id_))
                salida.append(f"{etiqueta}: corregido a {fecha + timedelta(days=dias)}")
    conn.commit()
    for s in salida:
        if "corregido" in s and "ya estaba" not in s:
            log.info("corrección de pronóstico: %s", s)
    return salida
