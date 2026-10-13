"""
Mapas de observaciones de superficie de Ogimet:

    https://www.ogimet.com/cgi-bin/gsynop?zona=amersurs&base=bluem&proy=orto&ano=2026&mes=10&day=12&hora=12&vall=All&enviar=Ver

Una sola consulta genera ~12 mapas de esa hora (temperatura, rocío, tiempo
significativo, precipitación...) como imágenes TEMPORALES:

    <IMG SRC="../tmp/202610121200_amersurs_Te.png" USEMAP="#g_Te" ALT="TEMPERATURA (Celsius)">

Hay que bajarlas enseguida (Ogimet las borra al rato). El nombre trae la
fecha y hora: se controla que sea la pedida antes de guardar. Solo hay
mapas del último día, y se acepta una consulta cada 20 s (ver
ingesta/fuentes/ogimet.py).

Esas imágenes son TRANSPARENTES: solo traen los valores de las estaciones.
La geografía (costas, límites: base=bluem) la pone la página por debajo,
como imagen de fondo aparte. Esa imagen no cambia de un día a otro: se baja
una vez por zona y el visualizador la pone debajo de cada mapa.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

URL_PAGINA = "https://www.ogimet.com/cgi-bin/gsynop"
URL_TMP = "https://www.ogimet.com/tmp/"
_IMG = re.compile(r'<IMG\s+SRC="\.\./tmp/(\d{12})_([A-Za-z0-9]+)_([A-Za-z0-9]+)\.png"[^>]*?ALT="([^"]*)"', re.I)


# Cualquier imagen que nombre la página: src="...", background="...", url(...)
_REF = re.compile(r'(?:src|background)\s*=\s*["\']?([^"\'\s>]+\.(?:png|jpe?g|gif))|url\(\s*["\']?([^"\')\s]+\.(?:png|jpe?g|gif))', re.I)
_MAPA_DEL_DIA = re.compile(r"/tmp/\d{12}_", re.I)


# Para los mapas que Ogimet tiene que GENERAR (fechas pasadas), responde vacío
# si el pedido no dice que viene de su propia página del formulario (header
# Referer), como hace el navegador. Probado el 13/10/2026: sin Referer, 0
# bytes; con Referer, la página con los mapas. Los del día ya están armados
# y salían igual: por eso al principio pareció que "solo tenía el último día".
ENCABEZADOS = {
    "Referer": "https://www.ogimet.com/gsynop.phtml",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.8",
}


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


def referencias(html):
    """Todas las imágenes que nombra la página (para el diagnóstico)."""
    vistas = []
    for a, b in _REF.findall(html):
        u = a or b
        if u not in vistas:
            vistas.append(u)
    return vistas


def fondos(html, zona, base="bluem"):
    """Direcciones de la imagen de fondo (la geografía) de esa zona: las
    imágenes de la página que no son los mapas del día y nombran la zona o
    la base. La primera es la más probable."""
    salida = []
    for u in referencias(html):
        if _MAPA_DEL_DIA.search(u) or not re.search(f"{zona}|{base}", u, re.I):
            continue
        url = urljoin(URL_PAGINA, u)
        if url not in salida:
            salida.append(url)
    return salida


def es_transparente(datos):
    """¿El PNG tiene transparencia? (canal alfa, o paleta con tRNS)"""
    if datos[:8] != b"\x89PNG\r\n\x1a\n" or len(datos) < 26:
        return False
    tipo_color = datos[25]
    return tipo_color in (4, 6) or b"tRNS" in datos[:4096]
