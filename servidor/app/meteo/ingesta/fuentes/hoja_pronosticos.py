"""
Historial COMPLETO de pronósticos desde la hoja "Pronosticos" del portal.

Cada fila de esa hoja es una publicación, y la columna json_completo trae
el JSON entero tal cual lo armó el pronosticador: el mismo formato que el
servidor captura en vivo (períodos, probabilidad de lluvia, ráfagas...).
Es mejor que la hoja Verificacion_Pronostico, que trae todo resumido y
no tiene el Día 1.

- La huella se calcula sobre el JSON publicado: lo que el servidor ya
  capturó en vivo se reconoce y no se duplica.
- Se agrega (sin tocar la huella) de dónde vino y cuándo se publicó
  (timestamp_guardado, con hora: sirve para ver errores de fecha).
- Se aplican las correcciones de config/correcciones_pronosticos.yaml.
- Las emisiones resumidas de la hoja de verificación de la misma ciudad y
  día se reemplazan por la completa.

Límite: la hoja guarda solo la última versión de cada publicación (las
correcciones pisan la fila), y un turno noche puede haber pisado la
publicación de la mañana del mismo día.
"""
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone

from . import hoja_verificacion, pronosticos

ORIGEN = "hoja Pronosticos"
HORA_ARG = timezone(timedelta(hours=-3))
# Una publicación para el día siguiente guardada antes de esta hora
# (argentina) probablemente fue la casilla "para mañana" tildada por error.
HORA_TURNO_NOCHE = 15


def leer_csv(texto):
    """CSV de la hoja -> [(emisión tal cual se publicó, momento de publicación)]."""
    csv.field_size_limit(sys.maxsize)
    salida, malas = [], 0
    for f in csv.DictReader(io.StringIO(texto.lstrip("﻿"))):
        if not (f.get("city") or "").strip():
            continue                      # filas vacías
        try:
            p = json.loads(f["json_completo"])
            p["forecast_date"]
        except (KeyError, TypeError, ValueError):
            malas += 1
            continue
        p.pop("team_password", None)      # la clave del equipo no se guarda
        try:
            ts = datetime.fromisoformat(f["timestamp_guardado"].strip().replace("Z", "+00:00"))
        except (KeyError, ValueError):
            ts = None
        salida.append((p, ts))
    return salida, malas


def sospechosas(emisiones):
    """Publicadas para el día siguiente a la MAÑANA: ¿"para mañana" por error?"""
    for p, ts in emisiones:
        if ts:
            local = ts.astimezone(HORA_ARG)
            if p["forecast_date"] > local.date().isoformat() and local.hour < HORA_TURNO_NOCHE:
                yield p, local


def importar(conn, texto):
    from .. import correcciones
    emisiones, malas = leer_csv(texto)
    extras = []
    for p, ts in emisiones:
        e = {"origen": ORIGEN}
        if ts:
            e["publicado_el"] = [ts.astimezone(HORA_ARG).date().isoformat()]
            e["publicado_ts"] = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            e["capturado"] = ts
        extras.append(e)
    nuevas = pronosticos.guardar(conn, [p for p, _ in emisiones], extras)
    lineas_corr = correcciones.aplicar(conn, correcciones.cargar())
    # Las resumidas (hoja de verificación) que ya tienen su versión completa.
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM pronostico_emision r
            WHERE r.contenido ->> 'origen' = %s
              AND EXISTS (SELECT 1 FROM pronostico_emision c
                          WHERE c.ciudad = r.ciudad AND c.fecha_pronostico = r.fecha_pronostico
                            AND c.contenido ->> 'origen' IS DISTINCT FROM %s)""",
                    (hoja_verificacion.ORIGEN, hoja_verificacion.ORIGEN))
        reemplazadas = cur.rowcount
    conn.commit()
    anotadas = {(str(c["ciudad"]), str(c["fecha_guardada"]), str(c["publicado_por"]))
                for c in correcciones.cargar()}
    avisos = [f"{p.get('city')}: guardada el {local:%d/%m %H:%M} como del {p['forecast_date']} ({p.get('publicado_por')})"
              for p, local in sospechosas(emisiones)
              if (p.get("city"), p["forecast_date"], p.get("publicado_por")) not in anotadas]
    return {"leidas": len(emisiones), "malas": malas, "nuevas": nuevas, "ya_estaban": len(emisiones) - nuevas,
            "resumidas_reemplazadas": reemplazadas, "correcciones": lineas_corr, "sospechosas_sin_corregir": avisos}
