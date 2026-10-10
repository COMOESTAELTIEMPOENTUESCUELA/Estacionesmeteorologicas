"""La proyección del GOES, verificada contra valores calculados con pyproj."""
import numpy as np
import pytest

from meteo.satelite.proyeccion import GOES_ESTE, latlon_a_xy, ventana_de_recorte, xy_a_latlon
from meteo.satelite.s3 import parsear_clave, tiempo_nominal


@pytest.mark.parametrize("lat,lon,x,y", [
    (-37.5, -61.0, 0.03271845667646441, -0.10330199232521993),
    (-34.9, -57.9, 0.04121940192870281, -0.09734695559549235),
])
def test_latlon_a_xy_igual_a_pyproj(lat, lon, x, y):
    xc, yc = latlon_a_xy(lat, lon, GOES_ESTE)
    assert xc == pytest.approx(x, abs=1e-9) and yc == pytest.approx(y, abs=1e-9)


def test_ida_y_vuelta():
    lat, lon = np.array([-34.9, -10.0, 5.0]), np.array([-57.9, -40.0, -80.0])
    la, lo = xy_a_latlon(*latlon_a_xy(lat, lon, GOES_ESTE), GOES_ESTE)
    assert np.allclose(la, lat) and np.allclose(lo, lon)


def test_lado_oculto_de_la_tierra():
    x, _ = latlon_a_xy(0.0, 100.0, GOES_ESTE)
    assert np.isnan(x)


def test_ventana_cubre_la_region():
    # grilla de disco completo de 2 km (banda 13)
    paso = 5.6e-05
    x = -0.151844 + paso * np.arange(5424)
    y = 0.151844 - paso * np.arange(5424)
    f0, f1, c0, c1 = ventana_de_recorte(x, y, (-61, -56, -37.5, -33), GOES_ESTE)
    for lat in (-37.5, -33):
        for lon in (-61, -56):
            xc, yc = latlon_a_xy(lat, lon, GOES_ESTE)
            assert x[c0] <= xc <= x[c1 - 1] and y[f1 - 1] <= yc <= y[f0]


def test_nombre_de_archivo():
    a = parsear_clave("ABI-L2-CMIPF/2026/283/12/OR_ABI-L2-CMIPF-M6C13_G19_s20262831200215_e20262831209534_c2.nc")
    assert a.banda == 13 and a.satelite == "G19"
    assert a.inicio.isoformat() == "2026-10-10T12:00:21+00:00"
    assert tiempo_nominal(a.inicio).strftime("%H:%M") == "12:00"
