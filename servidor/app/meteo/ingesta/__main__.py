"""
Uso (dentro del contenedor, o con DATABASE_URL apuntando a la base):

    python -m meteo.ingesta servicio
        Loop permanente: lo que corre Docker.

    python -m meteo.ingesta una thingspeak
        Corre una sola tarea ahora (thingspeak | wunderground | davis | ogimet |
        comunidad | pronosticos | verificacion) y termina. Útil para probar.

    python -m meteo.ingesta historico ogimet --desde 2026-01-01 --hasta 2026-10-01 [--estaciones omm_87593]
        Rellena el pasado. ThingSpeak y Wunderground guardan historia (con
        límites); Ogimet tiene años; la Davis solo los últimos días.

    python -m meteo.ingesta historico verificacion --desde 2026-01-01 --hasta 2026-10-01
        Recalcula lo observado para verificar pronósticos en ese rango
        (después de rellenar Ogimet del mismo período).

    python -m meteo.ingesta importar-hoja verificacion.csv     (o "-" para leer de la entrada)
        Importa el historial de pronósticos exportado de la hoja
        Verificacion_Pronostico del equipo. Se puede repetir: lo ya importado
        no se duplica. Desde fuera del contenedor:
        docker compose exec -T ingesta python -m meteo.ingesta importar-hoja - < archivo.csv

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
    ap.add_argument("comando", choices=["servicio", "una", "historico", "estaciones", "importar-hoja"])
    ap.add_argument("tarea", nargs="?")
    ap.add_argument("--config", default=os.environ.get("ESTACIONES_CONFIG", "/config/estaciones.yaml"))
    ap.add_argument("--desde", type=fecha_utc)
    ap.add_argument("--hasta", type=fecha_utc)
    ap.add_argument("--estaciones", help="ids separados por coma")
    args = ap.parse_intermixed_args()   # permite "importar-hoja --config x.yaml -"

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = cargar_config(args.config)
    conn = db.conectar()
    db.sincronizar_estaciones(conn, cfg["estaciones"])
    ing = Ingesta(cfg, conn)
    ids = set(args.estaciones.split(",")) if args.estaciones else None

    if args.comando == "importar-hoja":
        import sys
        from .fuentes import hoja_verificacion
        if not args.tarea:
            ap.error("falta el archivo CSV (o - para leerlo de la entrada)")
        texto = sys.stdin.read() if args.tarea == "-" else open(args.tarea, encoding="utf-8").read()
        nuevas, salteadas = hoja_verificacion.importar(conn, texto)
        print(f"{nuevas} emisiones importadas; {salteadas} ya estaban capturadas en vivo por el servidor.")
    elif args.comando == "servicio":
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
