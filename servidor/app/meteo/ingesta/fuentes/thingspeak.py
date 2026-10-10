"""
ThingSpeak: https://api.thingspeak.com/channels/<id>/feeds.json

Cada canal tiene hasta 8 "fields" (campos) numerados; qué variable es cada
uno lo define quien programó la estación, por eso el mapeo está en
estaciones.yaml. Cada campo puede llevar `factor` (multiplica el valor) o
`mm_por_pulso` (pluviómetro de cazoleta: valor > 0 = un vuelco). La API devuelve como máximo 8000 registros por pedido, así
que los rangos largos se piden por partes.
"""
from datetime import datetime, timedelta, timezone

from ..base import Obs, num

URL = "https://api.thingspeak.com/channels/{id}/feeds.json"
MAX_RESULTADOS = 8000


def parsear_feeds(feeds, estacion_id, campos):
    """Convierte la lista `feeds` de la API en observaciones."""
    obs = []
    for f in feeds:
        creado = f.get("created_at")
        if not creado:
            continue
        ts = datetime.fromisoformat(creado.replace("Z", "+00:00"))
        for campo, destino in campos.items():
            if isinstance(destino, str):
                destino = {"variable": destino}
            v = num(f.get(campo))
            if v is None:
                continue
            if "mm_por_pulso" in destino:
                # Pluviómetro de cazoleta: cada lectura con valor > 0 es UN vuelco
                # (un pulso), sin importar el número crudo del campo. Es la misma
                # regla que usan red-meteorologica.html y la Comparación de Pluviómetros.
                if v < 0:
                    continue
                v = float(destino["mm_por_pulso"]) if v > 0 else 0.0
            else:
                v = v * float(destino.get("factor", 1.0))
            obs.append(Obs(ts, estacion_id, destino["variable"], round(v, 4)))
    return obs


def traer(estacion, desde, hasta, sesion):
    cfg = estacion["config"]
    obs = []
    for canal in cfg["canales"]:
        inicio = desde
        while inicio < hasta:
            params = {
                "start": inicio.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "end": hasta.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "UTC",
                "results": MAX_RESULTADOS,
            }
            if canal.get("key"):
                params["api_key"] = canal["key"]
            r = sesion.get(URL.format(id=canal["id"]), params=params, timeout=60)
            r.raise_for_status()
            feeds = (r.json() or {}).get("feeds") or []
            obs += parsear_feeds(feeds, estacion["id"], cfg["campos"])
            if len(feeds) < MAX_RESULTADOS:
                break
            # Llegó al tope: seguir desde el último registro recibido.
            ultimo = datetime.fromisoformat(feeds[-1]["created_at"].replace("Z", "+00:00"))
            inicio = ultimo + timedelta(seconds=1)
    return obs
