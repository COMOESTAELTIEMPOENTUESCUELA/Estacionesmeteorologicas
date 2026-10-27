"""Cartas de diagnóstico propias (GFS): índice, decodificación y cuentas."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

UTC = timezone.utc

IDX = """1:0:d=2026102612:PRMSL:mean sea level:anl:
2:100:d=2026102612:CLMR:1 hybrid level:anl:
3:250:d=2026102612:HGT:500 mb:anl:
4:400:d=2026102612:TMP:500 mb:anl:
5:600:d=2026102612:HGT:850 mb:anl:
"""


def test_indice_y_tramos():
    from meteo.cartas import gfs
    ind = gfs.leer_indice(IDX)
    assert ind[("PRMSL", "mean sea level")] == (0, 99)
    assert ind[("HGT", "850 mb")] == (600, None)            # el último: hasta el final del archivo
    t = gfs.tramos(ind, {("HGT", "500 mb"), ("TMP", "500 mb"), ("PRMSL", "mean sea level")})
    # 500 mb HGT y TMP están seguidos: un solo pedido para los dos.
    assert [(a, b, [k for k, *_ in p]) for a, b, p in t] == [
        (0, 99, [("PRMSL", "mean sea level")]),
        (250, 599, [("HGT", "500 mb"), ("TMP", "500 mb")])]
    with pytest.raises(KeyError):
        gfs.tramos(ind, {("ABSV", "500 mb")})


def test_urls_del_analisis():
    from meteo.cartas import gfs
    u = gfs.urls(datetime(2026, 10, 26, 12, tzinfo=UTC))
    assert u[0] == "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261026/12/atmos/gfs.t12z.pgrb2.0p50.f000"


def test_decodificar_grib2_recorta_y_pasa_a_longitudes_negativas():
    eccodes = pytest.importorskip("eccodes")
    from meteo.cartas import gfs
    # Un mensaje GRIB2 de grilla global de 1° como los del GFS: de norte a sur,
    # longitudes 0..359.
    gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    for k, v in {"Ni": 360, "Nj": 181, "latitudeOfFirstGridPointInDegrees": 90,
                 "longitudeOfFirstGridPointInDegrees": 0, "latitudeOfLastGridPointInDegrees": -90,
                 "longitudeOfLastGridPointInDegrees": 359, "iDirectionIncrementInDegrees": 1,
                 "jDirectionIncrementInDegrees": 1, "jScansPositively": 0}.items():
        eccodes.codes_set(gid, k, v)
    lat = np.arange(90, -91, -1.0)[:, None]
    lon = np.arange(0, 360, 1.0)[None, :]
    eccodes.codes_set_values(gid, (lat * 1000 + lon).ravel())   # valor = lat*1000 + lon (0..359)
    mensaje = eccodes.codes_get_message(gid)
    eccodes.codes_release(gid)
    c = gfs.decodificar(mensaje, (-70, -50, -40, -20))
    assert c.lat[0] > c.lat[-1]                                   # de norte a sur
    assert c.lon.min() >= -95 and c.lon.max() <= -25              # con margen, en -180..180
    i, j = list(c.lat).index(-35.0), list(c.lon).index(-60.0)
    assert abs(c.valores[i, j] - (-35 * 1000 + 300)) < 0.5         # -60° es el 300 de la grilla original


def test_theta_e_ivt_y_vorticidad():
    from meteo.cartas import diagnostico as d
    # Aire casi seco: θe ≈ θ = T (1000/p)^0.2854.
    seco = d.theta_e(np.array(280.0), np.array(1.0), 850.0)
    assert abs(seco - 280 * (1000 / 850) ** 0.2854) < 0.5
    # Aire cálido y húmedo de 850 hPa (20 °C, 80 %): θe de una masa tropical.
    assert 335 < d.theta_e(np.array(293.15), np.array(80.0), 850.0) < 350
    # IVT con q y viento uniformes entre 1000 y 300 hPa: q·u·Δp/g.
    niveles = d.NIVELES_IVT
    q = np.full((len(niveles), 2, 2), 0.01)
    u = np.full_like(q, 10.0)
    v = np.zeros_like(q)
    psup = np.array([[1013.0, 1013.0], [1013.0, 600.0]])            # un punto en la cordillera
    iu, iv = d.ivt(q, u, v, niveles, psup)
    assert abs(iu[0, 0] - 0.01 * 10 * 70000 / d.G) < 1 and iv[0, 0] == 0
    assert iu[1, 1] < iu[0, 0] / 2                                  # bajo el suelo no cuenta
    # Vorticidad relativa = absoluta - f: si la absoluta es f, la relativa es 0.
    lat = np.array([-35.0])
    f = 2 * d.OMEGA * np.sin(np.radians(lat))[:, None]
    assert abs(d.vorticidad_relativa(f, lat)).max() < 1e-12
    # El suavizado conserva un campo uniforme.
    assert np.allclose(d.suavizar(np.full((5, 5), 7.0)), 7.0)


def test_un_analisis_dibuja_las_cuatro(tmp_path, monkeypatch):
    from meteo.cartas import descarga, diagnostico, gfs
    bajadas = []

    def bajar(ts, pedidos, region, sesion):
        if ts.hour == 18:
            raise FileNotFoundError("todavía no está publicado")
        bajadas.append(ts)
        return {"campos": ts}

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    monkeypatch.setattr(gfs, "bajar", bajar)
    monkeypatch.setattr(diagnostico, "dibujar", lambda campos, ts, region: {k: png for k in diagnostico.CARTAS})
    productos = [{"id": f"gfs_{k}", "fuente": "gfs", "carta": k} for k in diagnostico.CARTAS]
    desde, hasta = datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 26, 18, tzinfo=UTC)
    assert descarga._correr_gfs(productos, tmp_path, None, (0, 6, 12, 18), desde, hasta) == 12   # 3 horas x 4
    assert len(bajadas) == 3                                        # un solo análisis por hora
    assert descarga._correr_gfs(productos, tmp_path, None, (0, 6, 12, 18), desde, hasta) == 0    # ya están
    assert descarga.archivo.existe(tmp_path, "gfs_ivt", datetime(2026, 10, 26, 12, tzinfo=UTC))


def test_descargar_false_no_baja_pero_se_ve(tmp_path):
    """Bentley queda con descargar: false: no se le pide nada."""
    from meteo.cartas import descarga

    class Sesion:
        def get(self, url, **k):
            raise AssertionError(f"no tendría que pedir {url}")

    cfg = {"productos": [{"id": "b1", "fuente": "bentley", "dominio": "southamer", "variable": "x",
                          "descargar": False}]}
    assert descarga._correr(cfg, tmp_path, Sesion()) == 0
