"""
Uso:
    python -m meteo.satelite servicio                  # loop permanente (lo usa Docker)
    python -m meteo.satelite ahora                     # una sola vuelta y termina
    python -m meteo.satelite historico --desde 2026-10-14T18:00 --hasta 2026-10-15T06:00 \
                                       [--productos ir13_centro,wv09_centro]
    python -m meteo.satelite redibujar --desde ... --hasta ...   # regenera PNG desde los .nc

`historico` sirve para armar el archivo de un evento pasado. NOAA guarda en el
bucket TODO desde que el satélite empezó a operar, así que se puede reconstruir
cualquier tormenta desde 2025 (GOES-19) o desde 2017 (cambiando el bucket a
noaa-goes16 en la config).
"""
import argparse
import logging
import os
from datetime import datetime, timezone

from .ciclo import Servicio, cargar_config


def fecha_utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser(prog="python -m meteo.satelite")
    ap.add_argument("comando", choices=["servicio", "ahora", "historico", "redibujar"])
    ap.add_argument("--config", default=os.environ.get("SATELITE_CONFIG", "/config/satelite.yaml"))
    ap.add_argument("--archivo", default=os.environ.get("ARCHIVO_DIR", "/archivo"))
    ap.add_argument("--desde", type=fecha_utc, help="UTC, ej. 2026-10-14T18:00")
    ap.add_argument("--hasta", type=fecha_utc, help="UTC")
    ap.add_argument("--productos", help="ids separados por coma (por defecto, todos)")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    servicio = Servicio(cargar_config(args.config), args.archivo)
    servicio.escribir_catalogo()

    if args.comando == "servicio":
        servicio.para_siempre()
    elif args.comando == "ahora":
        servicio.una_vuelta()
    else:
        if not (args.desde and args.hasta):
            ap.error(f"{args.comando} necesita --desde y --hasta")
        solo = set(args.productos.split(",")) if args.productos else None
        if args.comando == "historico":
            servicio.correr_productos(args.desde, args.hasta, solo)
        else:
            servicio.redibujar(args.desde, args.hasta, solo)


if __name__ == "__main__":
    main()
