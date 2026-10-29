"""
Pronóstico del GFS de la página de Alicia Bentley: todos los plazos que
publica (cada 6 h, ~8 días), SOLO de la última corrida.

Se guarda en ARCHIVO_DIR/cartas/_pronostico/<producto>/:
    corrida.json                               qué corrida es y qué plazos hay
    <producto>_<corrida AAAAMMDDHH>_f024.png   un archivo por plazo (f024 = +24 h)

La corrida va en el nombre de cada imagen: así el navegador puede guardarlas
en caché para siempre sin mostrar nunca una de otra corrida.

Cuando sale una corrida nueva se baja completa a una carpeta aparte y recién
al terminar reemplaza a la anterior: nunca queda un pronóstico a medias.
No crece: ~25 MB fijos para las 4 cartas.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

CARPETA = "_pronostico"


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


def publicar(raiz, producto, tmp, inicio, cuadros):
    """Escribe corrida.json y reemplaza la corrida anterior por la nueva.
    cuadros: [(plazo, valida, archivo)]."""
    (tmp / "corrida.json").write_text(json.dumps({
        "inicio": inicio.isoformat(),
        "cuadros": [{"plazo_h": p, "valida": v.isoformat(), "archivo": a} for p, v, a in cuadros],
    }, ensure_ascii=False), encoding="utf-8")
    final = carpeta(raiz, producto)
    vieja = final.with_name(producto + ".vieja")
    shutil.rmtree(vieja, ignore_errors=True)
    if final.exists():
        final.rename(vieja)
    tmp.rename(final)
    shutil.rmtree(vieja, ignore_errors=True)


def descartar(tmp):
    shutil.rmtree(tmp, ignore_errors=True)
