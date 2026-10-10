"""
Pronósticos de Pronóstico UNLP.

El Apps Script de Pronostico-unlp devuelve, para cada ciudad, la emisión
VIGENTE (?city=La Plata). No guarda un historial consultable, así que el
servidor la consulta cada hora y se queda con cada emisión distinta que ve.
Con eso, en unos meses, se puede verificar el pronóstico contra lo observado
(Ogimet): ¿cuánto se erró la máxima a 1, 2, 3 días de plazo?

Cada emisión se guarda completa (JSON) y además "desarmada" por día
pronosticado en pronostico_dia, que es lo cómodo para analizar.
"""
import hashlib
import json
from datetime import date

from psycopg.types.json import Jsonb

from ..base import num


def huella(p):
    """Identifica una emisión. Si cambia CUALQUIER cosa (una corrección,
    otra hora de emisión), es una emisión nueva."""
    limpio = {k: v for k, v in p.items() if k not in ("team_password",)}
    return hashlib.sha256(json.dumps(limpio, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def desarmar(p):
    """Emisión -> filas por día pronosticado."""
    emitido = date.fromisoformat(p["forecast_date"])
    filas = []
    for d in p.get("daily_forecasts") or []:
        try:
            dia = date.fromisoformat(d["date"])
        except (KeyError, ValueError):
            continue
        filas.append({
            "fecha": dia,
            "plazo_dias": (dia - emitido).days,
            "temp_min": num(d.get("temp_min")),
            "temp_max": num(d.get("temp_max")),
            "temp_min_suburbana": num(d.get("temp_min_suburban")),
            "temp_max_suburbana": num(d.get("temp_max_suburban")),
            "periodos": Jsonb(d.get("period_forecasts") or []),
        })
    return filas


def traer(cfg, sesion):
    emisiones = []
    for ciudad in cfg["ciudades"]:
        r = sesion.get(cfg["url"], params={"city": ciudad}, timeout=60)
        r.raise_for_status()
        p = r.json()
        if p.get("error") or not p.get("forecast_date"):
            continue
        p.pop("team_password", None)
        emisiones.append(p)
    return emisiones


def guardar(conn, emisiones):
    nuevas = 0
    with conn.cursor() as cur:
        for p in emisiones:
            cur.execute(
                """INSERT INTO pronostico_emision (ciudad, fecha_pronostico, hora_emision, publicado_por,
                                                   es_correccion, contenido, huella)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (huella) DO NOTHING RETURNING id""",
                (p.get("city"), p["forecast_date"], p.get("emission_time"), p.get("publicado_por"),
                 p.get("es_correccion"), Jsonb(p), huella(p)),
            )
            fila = cur.fetchone()
            if not fila:
                continue  # ya la teníamos
            nuevas += 1
            for d in desarmar(p):
                cur.execute(
                    """INSERT INTO pronostico_dia (emision_id, fecha, plazo_dias, temp_min, temp_max,
                           temp_min_suburbana, temp_max_suburbana, periodos)
                       VALUES (%(emision_id)s, %(fecha)s, %(plazo_dias)s, %(temp_min)s, %(temp_max)s,
                               %(temp_min_suburbana)s, %(temp_max_suburbana)s, %(periodos)s)""",
                    {**d, "emision_id": fila[0]},
                )
    conn.commit()
    return nuevas
