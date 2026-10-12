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
