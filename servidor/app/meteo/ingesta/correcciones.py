"""
Correcciones a mano de pronósticos publicados con la fecha equivocada.

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

from .fuentes.pronosticos import correr_fechas

log = logging.getLogger(__name__)
RUTA = os.environ.get("CORRECCIONES_CONFIG", "/config/correcciones_pronosticos.yaml")


def cargar(ruta=RUTA):
    try:
        with open(ruta, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("correcciones") or []
    except FileNotFoundError:
        return []


def aplicar(conn, correcciones):
    """Aplica las que todavía no se aplicaron. Devuelve una línea por corrección."""
    salida = []
    with conn.cursor() as cur:
        for c in correcciones:
            fecha = date.fromisoformat(str(c["fecha_guardada"]))
            dias = int(c["mover_dias"])
            # La hora escrita en el formulario ayuda a no confundirla con
            # otra emisión de la misma persona; las importadas de la hoja no la tienen.
            cur.execute("""
                SELECT id, contenido FROM pronostico_emision
                WHERE ciudad = %s AND fecha_pronostico = %s AND publicado_por = %s
                  AND NOT contenido ? 'correccion_manual'
                  AND (hora_emision IS NULL OR %s::text IS NULL OR hora_emision = %s::text)""",
                        (c["ciudad"], fecha, c["publicado_por"], c.get("hora_emision"), c.get("hora_emision")))
            filas = cur.fetchall()
            etiqueta = f"{c['ciudad']} {fecha} ({c['publicado_por']})"
            if not filas:
                cur.execute("""SELECT 1 FROM pronostico_emision WHERE ciudad = %s AND publicado_por = %s
                               AND contenido -> 'correccion_manual' ->> 'fecha_original' = %s""",
                            (c["ciudad"], c["publicado_por"], fecha.isoformat()))
                salida.append(f"{etiqueta}: {'ya estaba corregido' if cur.fetchone() else 'no está en el servidor'}")
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
        if "corregido a" in s:
            log.info("corrección de pronóstico: %s", s)
    return salida
