"""
Uso (dentro del contenedor, o con DATABASE_URL apuntando a la base):

    python -m meteo.ingesta servicio
        Loop permanente: lo que corre Docker.

    python -m meteo.ingesta una thingspeak
        Corre una sola tarea ahora (thingspeak | wunderground | davis | ogimet |
        comunidad | pronosticos | verificacion | cartas) y termina. Útil para probar.

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

    python -m meteo.ingesta reprocesar-synop [--desde 2026-08-01 --hasta 2026-10-01] [--estaciones omm_87640]
        Vuelve a decodificar los SYNOP ya guardados (tabla synop_crudo) con el
        decodificador actual, sin pedirle nada a Ogimet. Después de mejorar
        synop.py.

    python -m meteo.ingesta importar-emision archivo.json --mover-dias -1 --publicado-el 2026-10-09 --motivo "..."
        Carga a mano una emisión (el JSON de la columna json_completo de la
        hoja "Pronosticos") que el servidor no llegó a ver. Con --mover-dias
        se corrigen las fechas de una publicada con "para mañana" por error.
        Desde fuera del contenedor, con "-" en vez del archivo:
        docker compose exec -T ingesta python -m meteo.ingesta importar-emision - ... < emision.json

    python -m meteo.ingesta importar-pronosticos pronosticos.csv     (o "-" para leer de la entrada)
        Importa el historial COMPLETO de la hoja "Pronosticos" del portal
        (columna json_completo: con períodos, probabilidades, ráfagas).
        Lo ya capturado no se duplica, se aplican las correcciones y se
        reemplaza lo resumido que vino de la hoja de verificación.
        docker compose exec -T ingesta python -m meteo.ingesta importar-pronosticos - < archivo.csv

    python -m meteo.ingesta aplicar-correcciones
        Aplica config/correcciones_pronosticos.yaml (pronósticos publicados
        con la fecha equivocada) a lo ya guardado. Se puede repetir.

    python -m meteo.ingesta diagnostico-ogimet
        Mira cómo arma Ogimet sus mapas de superficie (qué imágenes trae la
        página, si son transparentes, cuál es el fondo con la geografía).

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
    ap.add_argument("comando", choices=["servicio", "una", "historico", "estaciones", "importar-hoja", "reprocesar-synop",
                                         "diagnostico-ogimet", "importar-emision",
                                         "aplicar-correcciones", "importar-pronosticos"])
    ap.add_argument("tarea", nargs="?")
    ap.add_argument("--config", default=os.environ.get("ESTACIONES_CONFIG", "/config/estaciones.yaml"))
    ap.add_argument("--desde", type=fecha_utc)
    ap.add_argument("--hasta", type=fecha_utc)
    ap.add_argument("--estaciones", help="ids separados por coma")
    ap.add_argument("--mover-dias", type=int, default=0)
    ap.add_argument("--publicado-el", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date().isoformat())
    ap.add_argument("--motivo", default="")
    args = ap.parse_intermixed_args()   # permite "importar-hoja --config x.yaml -"

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = cargar_config(args.config)
    conn = db.conectar()
    db.sincronizar_estaciones(conn, cfg["estaciones"])
    ing = Ingesta(cfg, conn)
    ids = set(args.estaciones.split(",")) if args.estaciones else None

    if args.comando == "diagnostico-ogimet":
        from ..cartas import descarga
        print("\n".join(descarga.diagnostico(os.environ.get("ARCHIVO_DIR", "/archivo"), ing.sesion)))
        return
    if args.comando == "importar-pronosticos":
        import sys
        from .fuentes import hoja_pronosticos
        if not args.tarea:
            ap.error("falta el archivo CSV (o - para leerlo de la entrada)")
        texto = sys.stdin.read() if args.tarea == "-" else open(args.tarea, encoding="utf-8").read()
        r = hoja_pronosticos.importar(conn, texto)
        print(f"{r['leidas']} publicaciones leídas ({r['malas']} filas con el JSON roto, salteadas).")
        print(f"  {r['nuevas']} nuevas; {r['ya_estaban']} ya estaban (capturadas en vivo, importadas antes o repetidas en la hoja por un doble clic).")
        print(f"  {r['resumidas_reemplazadas']} emisiones resumidas (hoja de verificación) reemplazadas por la completa.")
        print("Correcciones de fecha:")
        print("\n".join("  " + s for s in r["correcciones"]) or "  (ninguna anotada)")
        if r["sospechosas_sin_corregir"]:
            print("OJO, publicadas a la mañana para el día siguiente y SIN corrección anotada"
                  " (¿'para mañana' por error? revisar y agregar a config/correcciones_pronosticos.yaml):")
            print("\n".join("  " + s for s in r["sospechosas_sin_corregir"]))
        return
    if args.comando == "aplicar-correcciones":
        from . import correcciones
        lista = correcciones.cargar()
        print(f"{len(lista)} correcciones en {correcciones.RUTA}:")
        print("\n".join("  " + s for s in correcciones.aplicar(conn, lista)))
        return
    if args.comando == "importar-emision":
        import json
        import sys
        from .fuentes import pronosticos
        if not (args.tarea and args.publicado_el and args.motivo):
            ap.error("importar-emision necesita el archivo (o -), --publicado-el y --motivo")
        texto = sys.stdin.read() if args.tarea == "-" else open(args.tarea, encoding="utf-8").read()
        p = json.loads(texto)
        p.pop("team_password", None)
        p = pronosticos.correr_fechas(p, args.mover_dias, args.publicado_el, args.motivo)
        dias = ", ".join(f"{d['date']} ({d['day_name']}) {d.get('temp_min')}/{d.get('temp_max')}"
                         for d in p.get("daily_forecasts") or [])
        n = pronosticos.guardar(conn, [p])
        print(f"{p.get('city')}, pronóstico del {p['forecast_date']} ({p.get('publicado_por')}): "
              f"{'cargado' if n else 'ya estaba cargado'}.\n  Días: {dias}")
        return
    if args.comando == "importar-hoja":
        import sys
        from .fuentes import hoja_verificacion
        if not args.tarea:
            ap.error("falta el archivo CSV (o - para leerlo de la entrada)")
        texto = sys.stdin.read() if args.tarea == "-" else open(args.tarea, encoding="utf-8").read()
        nuevas, salteadas = hoja_verificacion.importar(conn, texto)
        print(f"{nuevas} emisiones importadas; {salteadas} ya estaban capturadas en vivo por el servidor.")
    elif args.comando == "reprocesar-synop":
        from .synop import decodificar
        sql = "SELECT estacion_id, ts, mensaje FROM synop_crudo WHERE true"
        params = []
        if args.desde:
            sql += " AND ts >= %s"; params.append(args.desde)
        if args.hasta:
            sql += " AND ts <= %s"; params.append(args.hasta)
        if ids:
            sql += " AND estacion_id = ANY(%s)"; params.append(list(ids))
        with conn.cursor() as cur:
            cur.execute(sql, params)
            mensajes = cur.fetchall()
        obs = [o for est, ts, m in mensajes for o in decodificar(m, ts, est)]
        n = db.guardar_observaciones(conn, obs, "ogimet")
        print(f"{len(mensajes)} mensajes decodificados de nuevo: {len(obs)} valores, {n} nuevos o cambiados.")
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
