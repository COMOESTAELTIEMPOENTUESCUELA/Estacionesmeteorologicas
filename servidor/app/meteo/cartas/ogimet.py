"""
Mapas de observaciones de superficie de Ogimet:

    https://www.ogimet.com/cgi-bin/gsynop?zona=amersurs&base=bluem&proy=orto&ano=2026&mes=10&day=12&hora=12&vall=All&enviar=Ver

Una sola consulta genera ~12 mapas de esa hora (temperatura, rocío, tiempo
significativo, precipitación...) como imágenes TEMPORALES:

    <IMG SRC="../tmp/202610121200_amersurs_Te.png" USEMAP="#g_Te" ALT="TEMPERATURA (Celsius)">

Hay que bajarlas enseguida (Ogimet las borra al rato). El nombre trae la
fecha y hora: se controla que sea la pedida antes de guardar. Ogimet genera
los mapas de cualquier fecha, así que se puede rellenar el historial; pero
acepta una consulta cada 20 s (ver ingesta/fuentes/ogimet.py).
"""
import re
from datetime import datetime, timezone

URL_PAGINA = "https://www.ogimet.com/cgi-bin/gsynop"
URL_TMP = "https://www.ogimet.com/tmp/"
_IMG = re.compile(r'<IMG\s+SRC="\.\./tmp/(\d{12})_([A-Za-z0-9]+)_([A-Za-z0-9]+)\.png"[^>]*?ALT="([^"]*)"', re.I)


def parametros(zona, ts):
    return {"zona": zona, "base": "bluem", "proy": "orto", "ano": ts.year, "mes": f"{ts.month:02d}",
            "day": f"{ts.day:02d}", "hora": f"{ts.hour:02d}", "vall": "All", "enviar": "Ver"}


def imagenes(html):
    """HTML de la página -> {código: {"ts", "url", "alt"}}."""
    salida = {}
    for fecha, zona, codigo, alt in _IMG.findall(html):
        ts = datetime.strptime(fecha, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        salida[codigo] = {"ts": ts, "url": f"{URL_TMP}{fecha}_{zona}_{codigo}.png", "alt": alt}
    return salida
