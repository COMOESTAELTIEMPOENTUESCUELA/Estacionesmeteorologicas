"""
Pronóstico del GFS de la página de Alicia Bentley: todos los plazos que
publica (cada 6 h, ~8 días), SOLO de la última corrida.

Se guarda en ARCHIVO_DIR/cartas/_pronostico/<producto>/:
    corrida.json                               qué corrida es y qué plazos hay
    <producto>_<corrida AAAAMMDDHH>_f024.png   un archivo por plazo (f024 = +24 h)

La corrida va en el nombre de cada imagen: así el navegador puede guardarlas
en caché para siempre sin mostrar nunca una de otra corrida.

Cuando sale una corrida nueva se arma en una carpeta aparte y recién al
terminar reemplaza a la anterior: nunca se ve a medio bajar. Si la página
todavía no publicó todos los plazos de la corrida, se guardan los que hay y se
completan en las vueltas siguientes.
No crece: ~25 MB fijos para las 4 cartas.

Historial (las del GFS dibujadas en el servidor): con `conservar`, las
corridas anteriores de los últimos días quedan en la misma carpeta (sus
imágenes tienen la corrida en el nombre) y su corrida.json en `corridas/`.
Sirve para comparar corridas entre sí y el pronóstico con lo que pasó.
"""
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

CARPETA = "_pronostico"
HISTORIAL = "corridas"


def carpeta(raiz, producto):
    return Path(raiz) / "cartas" / CARPETA / producto


def nombre(producto, inicio, plazo):
    return f"{producto}_{inicio:%Y%m%d%H}_f{plazo:03d}.png"


def leer(raiz, producto):
    """El corrida.json de la última corrida guardada, o None."""
    f = carpeta(raiz, producto) / "corrida.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def corrida_guardada(raiz, producto):
    d = leer(raiz, producto)
    return datetime.fromisoformat(d["inicio"]) if d else None


def empezar(raiz, producto):
    """Carpeta temporal (vacía) donde se baja la corrida nueva."""
    tmp = carpeta(raiz, producto).with_name(producto + ".nueva")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    return tmp


def corridas(raiz, producto):
    """Todas las corridas guardadas (la última primero): [contenido de su corrida.json]."""
    salida = []
    d = leer(raiz, producto)
    if d:
        salida.append(d)
    for f in sorted((carpeta(raiz, producto) / HISTORIAL).glob("*.json"), reverse=True):
        try:
            salida.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return salida


def publicar(raiz, producto, tmp, inicio, cuadros, esperados=None, conservar=None, ahora=None, extra=None):
    """Escribe corrida.json y reemplaza la corrida anterior por la nueva.
    cuadros: [(plazo, valida, archivo)]; esperados: cuántos plazos tiene la
    corrida en la página (si hay menos, se completa en las vueltas siguientes).
    conservar (timedelta): las corridas anteriores que empezaron hace menos
    que eso siguen guardadas (historial); las más viejas se borran.
    extra: más datos para corrida.json (ej. el marco del mapa)."""
    (tmp / "corrida.json").write_text(json.dumps({
        "inicio": inicio.isoformat(),
        "esperados": esperados if esperados is not None else len(cuadros),
        "cuadros": [{"plazo_h": p, "valida": v.isoformat(), "archivo": a} for p, v, a in cuadros],
        **(extra or {}),
    }, ensure_ascii=False), encoding="utf-8")
    final = carpeta(raiz, producto)
    if conservar is not None and final.exists():
        limite = (ahora or datetime.now(timezone.utc)) - conservar
        (tmp / HISTORIAL).mkdir(exist_ok=True)
        for d in corridas(raiz, producto):
            ini = datetime.fromisoformat(d["inicio"])
            if ini == inicio or ini < limite:
                continue
            for c in d["cuadros"]:
                origen, destino = final / c["archivo"], tmp / c["archivo"]
                if origen.exists() and not destino.exists():
                    try:
                        os.link(origen, destino)          # mismo archivo, sin copiar (lo viejo sigue intacto)
                    except OSError:
                        shutil.copy2(origen, destino)
            (tmp / HISTORIAL / f"{ini:%Y%m%d%H}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    vieja = final.with_name(producto + ".vieja")
    shutil.rmtree(vieja, ignore_errors=True)
    if final.exists():
        final.rename(vieja)
    tmp.rename(final)
    shutil.rmtree(vieja, ignore_errors=True)


def descartar(tmp):
    shutil.rmtree(tmp, ignore_errors=True)
