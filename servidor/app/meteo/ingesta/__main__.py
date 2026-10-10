"""
Uso (dentro del contenedor, o con DATABASE_URL apuntando a la base):

    python -m meteo.ingesta servicio
        Loop permanente: lo que corre Docker.

    python -m meteo.ingesta una thingspeak
        Corre una sola tarea ahora (thingspeak | wunderground | davis | ogimet |
        comunidad | pronosticos) y termina. Útil para probar.

    python -m meteo.ingesta historico ogimet --desde 2026-01-01 --hasta 2026-10-01 [--estaciones omm_87593]
        Rellena el pasado. ThingSpeak y Wunderground guardan historia (con
        límites); Ogimet tiene años; la Davis solo los últimos días.

    python -m meteo.ingesta estaciones
        Copia estaciones.yaml a la base (también se hace al iniciar el servicio).
"""
import argparse
import logging
import os
from datetime import datetime, timezone

from . import db
from .tareas import Ingesta, cargar_config


def fecha_utc(s):
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser(prog="python -m meteo.ingesta")
    ap.add_argument("comando", choices=["servicio", "una", "historico", "estaciones"])
    ap.add_argument("tarea", nargs="?")
    ap.add_argument("--config", default=os.environ.get("ESTACIONES_CONFIG", "/config/estaciones.yaml"))
    ap.add_argument("--desde", type=fecha_utc)
    ap.add_argument("--hasta", type=fecha_utc)
    ap.add_argument("--estaciones", help="ids separados por coma")
    args = ap.parse_args()

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = cargar_config(args.config)
    conn = db.conectar()
    db.sincronizar_estaciones(conn, cfg["estaciones"])
    ing = Ingesta(cfg, conn)
    ids = set(args.estaciones.split(",")) if args.estaciones else None

    if args.comando == "servicio":
        ing.para_siempre()
    elif args.comando == "una":
        if not args.tarea:
            ap.error("falta la tarea, ej: python -m meteo.ingesta una thingspeak")
        ing.correr(args.tarea, ids=ids)
    elif args.comando == "historico":
        if not (args.tarea and args.desde and args.hasta):
            ap.error("historico necesita tarea, --desde y --hasta")
        # En el histórico se incluyen estaciones dadas de baja (ej. Daza).
        ing.correr(args.tarea, args.desde, args.hasta, ids, incluir_inactivas=True)


if __name__ == "__main__":
    main()
