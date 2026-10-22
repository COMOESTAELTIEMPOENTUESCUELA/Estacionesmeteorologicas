"""Cartas de diagnóstico: fechas de los cuadros de Bentley, archivo y descarga."""
from datetime import datetime, timedelta, timezone

import pytest

from meteo.cartas import archivo, bentley, descarga

# Fragmento real de standard.php (12/10/2026).
HTML = """
var minFrame = 1; //Minimum frame for every variable
var maxFrame = 89; //Maximum frame for every variable
var incrementFrame = 1; //Increment for every frame
var startFrame = 57; //Starting frame
function formatDate(offset,format){
	var newdate = String(2026101206
"""
UTC = timezone.utc


def test_leer_corrida_y_fechas_de_los_cuadros():
    c = bentley.leer_corrida(HTML)
    assert c == {"inicio": datetime(2026, 10, 12, 6, tzinfo=UTC), "start": 57, "min": 1, "max": 89}
    cuadros = bentley.cuadros_analisis(c)
    # hora válida = corrida + (N - 57) * 6 h (functions_gfs.js de la página)
    assert cuadros[0] == (57, datetime(2026, 10, 12, 6, tzinfo=UTC))
    assert (56, datetime(2026, 10, 12, 0, tzinfo=UTC)) in cuadros
    assert cuadros[-1] == (1, datetime(2026, 10, 12, 6, tzinfo=UTC) - timedelta(hours=6 * 56))   # 14 días antes
    assert len(cuadros) == 57                                   # solo análisis, nada de pronóstico
    assert len(bentley.cuadros_analisis(c, (12,))) == 14        # una por día


def test_pagina_sin_corrida_es_error():
    with pytest.raises(ValueError):
        bentley.leer_corrida("<html>otra cosa</html>")


def test_imagen_de_la_corrida_anterior_se_rechaza():
    inicio = datetime(2026, 10, 12, 6, tzinfo=UTC)
    assert bentley.es_de_esta_corrida({"Last-Modified": "Mon, 12 Oct 2026 10:41:00 GMT"}, inicio)
    assert not bentley.es_de_esta_corrida({"Last-Modified": "Mon, 12 Oct 2026 04:41:00 GMT"}, inicio)
    assert bentley.es_de_esta_corrida({}, inicio)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def test_archivo(tmp_path):
    ts = datetime(2026, 10, 4, 12, tzinfo=UTC)
    f = archivo.guardar(tmp_path, "bentley_rel_vort", ts, PNG)
    assert f.relative_to(tmp_path).as_posix() == "cartas/bentley_rel_vort/2026/10/bentley_rel_vort_2026100412.png"
    assert archivo.existe(tmp_path, "bentley_rel_vort", ts)
    with pytest.raises(ValueError):
        archivo.guardar(tmp_path, "x", ts, b"<html>error</html>")   # una página de error no es una carta
    assert archivo.disponibles(tmp_path, ["bentley_rel_vort"], "2026-10") == {"2026-10-04T12:00:00Z": ["bentley_rel_vort"]}


def test_descarga_completa_y_no_repite(tmp_path, monkeypatch):
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    pedidos = []

    class Resp:
        def __init__(self, contenido=b"", texto="", cab=None):
            self.content, self.text, self.headers = contenido, texto, cab or {}
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout):
            pedidos.append(url)
            if "standard.php" in url:
                return Resp(texto=HTML)
            return Resp(PNG, cab={"Last-Modified": "Mon, 12 Oct 2026 10:00:00 GMT"})

    cfg = {"horas_utc": [12], "productos": [
        {"id": "b_vort", "fuente": "bentley", "dominio": "southamer", "variable": "rel_vort"},
        {"id": "ogimet", "fuente": "ogimet", "activo": False}]}
    assert descarga.correr(cfg, tmp_path, Sesion()) == 14
    assert archivo.existe(tmp_path, "b_vort", datetime(2026, 10, 11, 12, tzinfo=UTC))
    # 11/10 12 UTC = corrida 12/10 06 UTC - 18 h = cuadro 54
    assert any(u.endswith("rel_vort_54.png") for u in pedidos)
    pedidos.clear()
    assert descarga.correr(cfg, tmp_path, Sesion()) == 0          # lo guardado no se vuelve a pedir
    assert len(pedidos) == 1                                       # solo la página


# Líneas reales de la página gsynop de Ogimet (12/10/2026 12 UTC).
HTML_OGIMET = """
<IMG src="/tmp/gfsmodel.gif" alt="Mapas modelo GFS" border="0" style="max-width: 100%">
<IMG SRC="../tmp/202610121200_amersurs_Te.png" USEMAP="#g_Te" ALT="TEMPERATURA (Celsius)" border="0">
<IMG SRC="../tmp/202610121200_amersurs_Sg.png" USEMAP="#g_Sg" ALT="TIEMPO SIGNIFICATIVO" border="0">
<IMG SRC="../tmp/202610121200_amersurs_R4.png" USEMAP="#g_R4" ALT="PRECIPITACION" border="0">
"""


def test_ogimet_imagenes_de_la_pagina():
    from meteo.cartas import ogimet
    imgs = ogimet.imagenes(HTML_OGIMET)
    assert set(imgs) == {"Te", "Sg", "R4"}                        # el gif del menú no cuenta
    assert imgs["Sg"]["url"] == "https://www.ogimet.com/tmp/202610121200_amersurs_Sg.png"
    assert imgs["Sg"]["ts"] == datetime(2026, 10, 12, 12, tzinfo=UTC)
    assert ogimet.parametros("amersurs", datetime(2026, 10, 12, 6, tzinfo=UTC))["hora"] == "06"


def test_ogimet_historial_y_control_de_fecha(tmp_path, monkeypatch):
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    pedidos = []

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            pedidos.append((url, params))
            if params:
                # Para las 18 UTC, Ogimet "se equivoca" y devuelve los mapas de las 12.
                hora = "12" if params["hora"] == "18" else params["hora"]
                return Resp(texto=HTML_OGIMET.replace("1200_", f"{hora}00_"))
            return Resp(PNG)

    cfg = {"horas_utc": [0, 6, 12, 18], "productos": [
        {"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"},
        {"id": "og_r4", "fuente": "ogimet", "zona": "amersurs", "codigo": "R4"}]}
    n = descarga.correr_ogimet(cfg, tmp_path, Sesion(), datetime(2026, 10, 12, 0, tzinfo=UTC),
                               datetime(2026, 10, 12, 18, tzinfo=UTC))
    assert n == 6                                                  # 00, 06 y 12 x 2 mapas; las 18 no
    assert not archivo.existe(tmp_path, "og_sg", datetime(2026, 10, 12, 18, tzinfo=UTC))
    assert sum(1 for _, p in pedidos if p) == 4                    # una página por hora
    pedidos.clear()
    descarga.correr_ogimet(cfg, tmp_path, Sesion(), datetime(2026, 10, 12, 0, tzinfo=UTC),
                           datetime(2026, 10, 12, 12, tzinfo=UTC))
    assert pedidos == []                                           # ya estaba todo


def test_cada_carta_con_sus_horas(tmp_path, monkeypatch):
    """La precipitación de 24 h y los extremos se informan a las 12 UTC: no se piden a otras horas."""
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    paginas = []

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            if params:
                paginas.append(params["hora"])
                return Resp(texto=HTML_OGIMET.replace("1200_", f"{params['hora']}00_"))
            return Resp(PNG)

    cfg = {"horas_utc": [0, 6, 12, 18], "productos": [
        {"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"},
        {"id": "og_r4", "fuente": "ogimet", "zona": "amersurs", "codigo": "R4", "horas_utc": [12]}]}
    n = descarga.correr_ogimet(cfg, tmp_path, Sesion(), datetime(2026, 10, 12, 0, tzinfo=UTC),
                               datetime(2026, 10, 12, 18, tzinfo=UTC))
    assert n == 5 and paginas == ["00", "06", "12", "18"]             # Sg x 4 + R4 solo a las 12
    assert not archivo.existe(tmp_path, "og_r4", datetime(2026, 10, 12, 6, tzinfo=UTC))
    assert archivo.existe(tmp_path, "og_r4", datetime(2026, 10, 12, 12, tzinfo=UTC))


def test_ogimet_rechazo_se_reintenta_y_no_dos_descargas(tmp_path, monkeypatch):
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    monkeypatch.setattr(descarga, "ESPERAS_OGIMET", (0,))
    respuestas = ["Sorry, your quota limit for slow queries rate has been reached", HTML_OGIMET]

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            return Resp(texto=respuestas.pop(0)) if params else Resp(PNG)

    cfg = {"horas_utc": [12], "productos": [{"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"}]}
    ts = datetime(2026, 10, 12, 12, tzinfo=UTC)
    assert descarga.correr_ogimet(cfg, tmp_path, Sesion(), ts, ts) == 1     # el rechazo se reintentó
    with descarga.una_sola_descarga(tmp_path):
        with pytest.raises(descarga.EnCurso):
            descarga.correr_ogimet(cfg, tmp_path, Sesion(), ts, ts)


def test_ogimet_respuesta_vacia_no_se_reintenta(tmp_path, monkeypatch):
    """Ogimet solo tiene los mapas del último día: para fechas viejas responde vacío."""
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    pedidos = []

    class Resp:
        content, text = b"", ""
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            pedidos.append(params["hora"])
            return Resp()

    cfg = {"horas_utc": [0, 6, 12, 18], "productos": [{"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"}]}
    assert descarga.correr_ogimet(cfg, tmp_path, Sesion(), datetime(2026, 10, 5, 0, tzinfo=UTC),
                                  datetime(2026, 10, 5, 18, tzinfo=UTC)) == 0
    assert pedidos == ["00", "06", "12", "18"]          # una sola vez cada hora, sin reintentos


def test_ogimet_fondo_y_transparencia():
    """Los mapas de Ogimet son transparentes; la geografía viene aparte."""
    from meteo.cartas import ogimet
    html = HTML_OGIMET + '<TD BACKGROUND="../img/bases/amersurs_bluem_orto.png"><img src="/img/ogimet.jpg">'
    assert ogimet.fondos(html, "amersurs") == ["https://www.ogimet.com/img/bases/amersurs_bluem_orto.png"]
    assert ogimet.fondos(HTML_OGIMET, "amersurs") == []          # los mapas del día no son el fondo
    ihdr = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 8 + b"\x08"
    assert ogimet.es_transparente(ihdr + b"\x06" + b"0" * 20)    # RGBA
    assert not ogimet.es_transparente(ihdr + b"\x02" + b"0" * 20)  # RGB
    assert ogimet.es_transparente(ihdr + b"\x03" + b"0" * 20 + b"tRNS")   # paleta con transparencia


def test_ogimet_baja_el_fondo_una_vez(tmp_path, monkeypatch):
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    fondos = []

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            if params:
                return Resp(texto=HTML_OGIMET.replace("1200_", f"{params['hora']}00_")
                            + '<TD background="../img/amersurs_bluem.png">')
            if "bluem" in url:
                fondos.append(url)
            return Resp(PNG)

    cfg = {"horas_utc": [0, 6], "productos": [{"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"}]}
    descarga.correr_ogimet(cfg, tmp_path, Sesion(), datetime(2026, 10, 12, 0, tzinfo=UTC), datetime(2026, 10, 12, 6, tzinfo=UTC))
    assert fondos == ["https://www.ogimet.com/img/amersurs_bluem.png"]     # dos horas, un solo fondo
    assert archivo.fondo(tmp_path, "ogimet_amersurs").relative_to(tmp_path).as_posix() == "cartas/_fondos/ogimet_amersurs.png"
    assert "_fondos" not in str(archivo.disponibles(tmp_path, ["og_sg"]))


def _png_pesado(ancho=1000, alto=750):
    """Una carta "como las de Bentley": PNG con ruido, de más de 100 KB."""
    import io
    import random

    from PIL import Image
    random.seed(1)
    im = Image.frombytes("RGB", (ancho, alto), bytes(random.getrandbits(8) for _ in range(ancho * alto * 3)))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_miniaturas(tmp_path):
    """Las cartas pesadas tienen una miniatura liviana para la placa; las
    livianas (Ogimet) no; y se completan las que falten."""
    from PIL import Image
    ts = datetime(2026, 10, 12, 12, tzinfo=UTC)
    pesada = _png_pesado()
    assert len(pesada) > archivo.MINIATURA_DESDE_BYTES
    archivo.guardar(tmp_path, "bentley_rel_vort", ts, pesada)
    m = archivo.ruta_miniatura(tmp_path, "bentley_rel_vort", ts)
    assert m.relative_to(tmp_path).as_posix() == "cartas/_min/bentley_rel_vort/2026/10/bentley_rel_vort_2026101212.webp"
    with Image.open(m) as im:
        assert im.size == (640, 480)                       # mantiene la proporción
    archivo.guardar(tmp_path, "ogimet_te", ts, PNG)          # liviana: sin miniatura
    assert not archivo.ruta_miniatura(tmp_path, "ogimet_te", ts).exists()
    assert archivo.disponibles(tmp_path, ["bentley_rel_vort"]) == {"2026-10-12T12:00:00Z": ["bentley_rel_vort"]}
    m.unlink()                                               # archivada antes de que existieran
    assert archivo.completar_miniaturas(tmp_path, ["bentley_rel_vort", "ogimet_te"]) == 1
    assert archivo.completar_miniaturas(tmp_path, ["bentley_rel_vort", "ogimet_te"]) == 0


# Lista de la carpeta de NOAA STAR (formato de los nombres: año, día del año, hora, minuto).
HTML_STAR = """
<a href="20262861150_GOES19-ABI-ssa-13-1800x1080.jpg">20262861150_GOES19-ABI-ssa-13-1800x1080.jpg</a>
<a href="20262861200_GOES19-ABI-ssa-13-1800x1080.jpg">20262861200_GOES19-ABI-ssa-13-1800x1080.jpg</a>
<a href="20262861200_GOES19-ABI-ssa-13-900x540.jpg">20262861200_GOES19-ABI-ssa-13-900x540.jpg</a>
<a href="20262861810_GOES19-ABI-ssa-13-1800x1080.jpg">20262861810_GOES19-ABI-ssa-13-1800x1080.jpg</a>
<a href="1800x1080.jpg">1800x1080.jpg</a>
"""


def test_star_elige_la_imagen_de_la_hora():
    from meteo.cartas import star
    d = star.imagenes(HTML_STAR)
    assert datetime(2026, 10, 13, 12, tzinfo=UTC) in d                      # día 286 de 2026 = 13/10
    assert star.elegir(d, datetime(2026, 10, 13, 12, tzinfo=UTC)) == "20262861200_GOES19-ABI-ssa-13-1800x1080.jpg"
    assert star.elegir(d, datetime(2026, 10, 13, 18, tzinfo=UTC)).startswith("20262861810")   # 10 min: vale
    assert star.elegir(d, datetime(2026, 10, 13, 6, tzinfo=UTC)) is None                       # no hay cerca
    assert star.url_carpeta({"sector": "ssa", "banda": "GEOCOLOR"}) == \
        "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/GEOCOLOR/"
    # Vapor de agua (08) y Air Mass: mismo formato de nombre (cada uno en su carpeta).
    wv = star.imagenes('<a href="20262951200_GOES19-ABI-ssa-08-1800x1080.jpg">')
    am = star.imagenes('<a href="20262951200_GOES19-ABI-ssa-AirMass-1800x1080.jpg"> '
                       '<a href="20262951200_GOES19-ABI-ssa-AirMass-900x540.jpg">')
    assert list(wv.values()) == ["20262951200_GOES19-ABI-ssa-08-1800x1080.jpg"]
    assert list(am.values()) == ["20262951200_GOES19-ABI-ssa-AirMass-1800x1080.jpg"]


def test_star_descarga_solo_las_horas_de_las_cartas(tmp_path, monkeypatch):
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "datetime", type("F", (datetime,), {"now": staticmethod(lambda tz=None: datetime(2026, 10, 13, 19, tzinfo=UTC))}))
    pedidos = []

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            pedidos.append(url.rsplit("/", 1)[-1] or "lista")
            return Resp(texto=HTML_STAR) if url.endswith("/") else Resp(b"\xff\xd8\xff" + b"0" * 50)

    p = {"id": "sat13", "fuente": "star", "sector": "ssa", "banda": "13"}
    assert descarga._correr_star(p, tmp_path, Sesion(), (0, 6, 12, 18)) == 2           # 12 y 18 UTC del 13
    assert archivo.existe(tmp_path, "sat13", datetime(2026, 10, 13, 12, tzinfo=UTC))
    assert archivo.ruta(tmp_path, "sat13", datetime(2026, 10, 13, 12, tzinfo=UTC), "jpg").exists()
    pedidos.clear()
    descarga._correr_star(p, tmp_path, Sesion(), (0, 6, 12, 18))
    assert pedidos == ["lista"]                    # lo que ya está no se vuelve a bajar


def test_historico_baja_satelite_y_ogimet(tmp_path, monkeypatch):
    """Rellenar 7 al 11/10: satélite y mapas de Ogimet del período."""
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    pedidos = []

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    lista = "".join(f"<a>2026{d:03d}{h:02d}00_GOES19-ABI-ssa-13-1800x1080.jpg</a>"
                    for d in range(280, 285) for h in (0, 6, 12, 18))     # 7 al 11/10

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            pedidos.append(url)
            if params:          # página de Ogimet de esa hora
                return Resp(texto=HTML_OGIMET.replace("202610121200", f"2026{params['mes']}{params['day']}{params['hora']}00"))
            return Resp(texto=lista) if url.endswith("/") else Resp(b"\xff\xd8\xff" + b"0" * 50)

    cfg = {"horas_utc": [0, 6, 12, 18], "productos": [
        {"id": "sat13", "fuente": "star", "sector": "ssa", "banda": "13"},
        {"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"}]}
    r = descarga.correr_historico(cfg, tmp_path, Sesion(), datetime(2026, 10, 7, tzinfo=UTC), datetime(2026, 10, 11, 18, tzinfo=UTC))
    assert r == {"sat13": 20, "ogimet": 20}                    # 5 días x 4 horas, de cada uno
    assert archivo.existe(tmp_path, "og_sg", datetime(2026, 10, 7, 12, tzinfo=UTC))


def test_ogimet_se_pide_como_desde_su_pagina(tmp_path, monkeypatch):
    """Sin Referer, Ogimet responde vacío para los mapas que tiene que generar."""
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            if params:
                if (headers or {}).get("Referer") != "https://www.ogimet.com/gsynop.phtml":
                    return Resp(texto="")                 # como Ogimet: vacío
                return Resp(texto=HTML_OGIMET.replace("202610121200", "202610071200"))
            return Resp(PNG)

    cfg = {"horas_utc": [12], "productos": [{"id": "og_sg", "fuente": "ogimet", "zona": "amersurs", "codigo": "Sg"}]}
    ts = datetime(2026, 10, 7, 12, tzinfo=UTC)
    assert descarga.correr_ogimet(cfg, tmp_path, Sesion(), ts, ts) == 1


# Formato real de las zonas clickeables de Ogimet (el texto del cartel ocupa varias líneas).
HTML_AREAS = """
<MAP name="g_Te">
<AREA HREF="./gsynres?ind=87593&ano=2026&mes=10&day=7&hora=12&min=0&ndays=30" COORDS="300,400,312,409" onMouseOver="return overlib('Lat: 34-58-00S Lon: 057-54-00W Alt: 23 m.<br>
<b>La Plata Aero</b><br>T: 12.0 &deg;C')" onMouseOut="return nd();">
<AREA HREF="./gsynres?ind=87576&ano=2026&mes=10&day=7&hora=12&min=0&ndays=30" COORDS="280,390,292,399" onMouseOver="return overlib('Lat: 34-49-00S Lon: 058-32-00W Alt: 20 m.<br>
Ezeiza Aero')" onMouseOut="return nd();">
</MAP>
<MAP name="g_Td">
<AREA HREF="./gsynres?ind=87593&ano=2026&mes=10&day=7&hora=12" COORDS="1,2,3,4" onMouseOver="return overlib('x')">
</MAP>
"""


def test_ogimet_zonas_clickeables():
    from meteo.cartas import ogimet
    z = ogimet.areas(HTML_AREAS)
    assert set(z) == {"Te", "Td"} and len(z["Te"]) == 2
    lp = z["Te"][0]
    assert lp["omm"] == "87593" and lp["caja"] == [300, 400, 312, 409]
    assert lp["texto"] == "Lat: 34-58-00S Lon: 057-54-00W Alt: 23 m.\nLa Plata Aero\nT: 12.0 °C"
    assert lp["enlace"].startswith("https://www.ogimet.com/cgi-bin/gsynres?ind=87593")



def test_nombres_de_estaciones_omm():
    """El cartel de Ogimet trae solo el número: el nombre sale de la lista."""
    from meteo.cartas import ogimet
    n = ogimet.nombres_omm()
    assert n["87593"] == "La Plata Aero" and n["87576"] == "Ezeiza Aero"
    assert n["86580"] == "Carrasco (UY)"            # de otro país, con el país
    assert len(n) > 700 and all(k.isdigit() and len(k) == 5 for k in n)

def test_ogimet_completa_zonas_sin_rebajar_la_imagen(tmp_path, monkeypatch):
    monkeypatch.setattr(descarga, "PAUSA_SEG", 0)
    monkeypatch.setattr(descarga, "PAUSA_OGIMET_SEG", 0)
    ts = datetime(2026, 10, 7, 12, tzinfo=UTC)
    archivo.guardar(tmp_path, "og_te", ts, PNG)                        # archivada antes de las zonas
    imagenes = []

    class Resp:
        def __init__(self, contenido=b"", texto=""):
            self.content, self.text = contenido, texto
        def raise_for_status(self):
            pass

    class Sesion:
        def get(self, url, timeout, params=None, headers=None):
            if params:
                return Resp(texto=HTML_OGIMET.replace("202610121200", "202610071200") + HTML_AREAS)
            imagenes.append(url)
            return Resp(PNG)

    cfg = {"horas_utc": [12], "productos": [{"id": "og_te", "fuente": "ogimet", "zona": "amersurs", "codigo": "Te"}]}
    assert descarga.correr_ogimet(cfg, tmp_path, Sesion(), ts, ts) == 0     # nada nuevo de imágenes
    assert imagenes == []
    import json
    zonas = json.loads(archivo.ruta_areas(tmp_path, "og_te", ts).read_text())
    assert [z["omm"] for z in zonas] == ["87593", "87576"]
