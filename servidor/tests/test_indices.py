"""Índices climáticos: los lectores de cada formato (con pedazos de los
archivos reales de NOAA y el BoM) y la interpretación de las fases."""
from datetime import date

from meteo import indices

ONI = """ SEAS  YR   TOTAL   ANOM
  DJF 2025  26.15  -0.62
  JFM 2025  26.44  -0.44
  NDJ 2024  25.82  -0.55
  SON 2025  26.20  -0.55
  OND 2025  26.10  -0.60
  NDJ 2025  26.00  -0.70
  DJF 2026  26.00  -0.70
  JFM 2026  26.30  -0.60
"""
SEMANAL = """ Weekly SST data starts week centered on 3Jan1990
                Nino1+2      Nino3        Nino34        Nino4
 Week          SST SSTA     SST SSTA     SST SSTA     SST SSTA
 03JAN1990     23.4-0.4     25.1-0.3     26.6 0.0     28.6 0.1
 22OCT2026     21.6 1.2     25.4 0.3     26.1-0.6     28.4-0.2
"""
SAM_DIARIO = """year,month,day,aao_index_cdas
2026,10,27,-1.234
2026,10,28,0.7
"""
SAM_MENSUAL = """ 1979    1  0.209
 2026    9 -0.512
"""
TABLA_PSL = """ 1948 2026
 2025   0.21   0.30   0.11   0.05  -0.10  -0.20  -0.30  -0.25  -0.40  -0.35  -0.20  -0.10
 2026   0.10   0.20   0.35   0.42   0.50   0.40 -99.99 -99.99 -99.99 -99.99 -99.99 -99.99
  -99.99
  TNA Tropical Northern Atlantic Index
"""
RMM = """RMM values up to "real time". For the last few days, the ACCESS analyses are used instead of NCEP.
year, month, day, RMM1, RMM2, phase, amplitude.  Missing Value= 1.E36 or 999
  1974   6   1  1.63447  1.20304  5  2.02948  Final_value:__OLR_&_NCEP_winds
  2026  10  27  1.E36  1.E36  999  1.E36  Missing_value
  2026  10  28  -0.81  1.02  7  1.30269  Prelim_value:__OLR_&_ACCESS_winds
"""


def test_lectores():
    oni = indices.leer_oni(ONI)
    assert oni[0] == (date(2025, 1, 1), -0.62, {"trimestre": "DJF"})
    assert oni[2][0] == date(2024, 12, 1)                          # NDJ 2024: centro en diciembre 2024
    s = indices.leer_nino_semanal(SEMANAL, 2)
    assert s == [(date(1990, 1, 3), 0.0, {"sst": 26.6}), (date(2026, 10, 22), -0.6, {"sst": 26.1})]
    assert indices.leer_nino_semanal(SEMANAL, 0)[1][1] == 1.2      # Niño 1+2
    assert indices.leer_diario(SAM_DIARIO) == [(date(2026, 10, 27), -1.234, None), (date(2026, 10, 28), 0.7, None)]
    assert indices.leer_mensual_columna(SAM_MENSUAL)[1] == (date(2026, 9, 1), -0.512, None)
    t = indices.leer_tabla_mensual(TABLA_PSL)
    assert len(t) == 18 and t[-1] == (date(2026, 6, 1), 0.4, None)   # sin los -99.99 ni la cabecera
    r = indices.leer_rmm(RMM)
    assert [x[0] for x in r] == [date(1974, 6, 1), date(2026, 10, 28)]   # el faltante se saltea
    assert r[1][1] == 1.3 and r[1][2] == {"fase": 7, "rmm1": -0.81, "rmm2": 1.02}


def test_fases():
    assert indices.fase("oni", -0.7) == ("La Niña débil", "-")
    assert indices.fase("oni", -1.2) == ("La Niña moderada", "-")
    assert indices.fase("oni", 2.3) == ("El Niño muy fuerte", "+")
    assert indices.fase("oni", 0.3) == ("Neutral", "0")
    assert indices.fase("sam", 0.7)[0] == "positivo"
    assert indices.fase("mjo", 0.6, {"fase": 3}) == ("débil (sin señal clara)", "0")
    assert indices.fase("mjo", 1.4, {"fase": 7}) == ("fase 7 (Pacífico oeste)", "+")
    assert indices.fase("soi", -1.3)[0] == "negativo (como El Niño)"


def test_episodio_enso():
    d = [(date(2025, m, 1), v, None) for m, v in enumerate([-0.3, -0.6, -0.7, -0.8, -0.6, -0.5, -0.2, -0.6], start=1)]
    assert indices.episodio_enso(d, 2) == "La Niña"          # 5 trimestres seguidos <= -0.5
    assert indices.episodio_enso(d, 7) is None              # uno suelto
    assert indices.episodio_enso(d, 0) is None


class _Sesion:
    def __init__(self, textos):
        self.textos = textos

    def get(self, url, timeout=None):
        sesion = self

        class R:
            text = sesion.textos.get(url, "")

            def raise_for_status(self):
                if url not in sesion.textos:
                    raise OSError("sin conexión")
        return R()


def test_bajar_y_estado(tmp_path):
    I = indices.INDICES
    textos = {I["oni"]["url"]: ONI, I["nino34"]["url"]: SEMANAL, I["sam"]["url"]: SAM_DIARIO,
              I["tna"]["url"]: TABLA_PSL, I["tsa"]["url"]: TABLA_PSL.replace("0.50", "0.10"), I["mjo"]["url"]: RMM}
    r = indices.bajar(tmp_path, _Sesion(textos))
    assert r["pdo"].startswith("FALLÓ") and r["oni"].startswith("8 datos")
    assert r["dipolo_atl"].startswith("18 datos")
    e = {x["id"]: x for x in indices.estado(tmp_path, date(2026, 10, 28), date(2026, 10, 1), date(2026, 10, 28))}
    assert e["sam"]["valor"]["valor"] == 0.7 and e["sam"]["valor"]["vigente"]
    assert e["sam"]["periodo"]["n"] == 2 and e["sam"]["periodo"]["media"] == -0.27
    assert e["mjo"]["valor"]["fase"] == "fase 7 (Pacífico oeste)"
    assert e["mjo"]["periodo"]["fase"].startswith("activa 100 %")
    assert e["oni"]["valor"]["fecha"] == "2026-02-01" and not e["oni"]["valor"]["vigente"]   # dato viejo
    assert e["dipolo_atl"]["valor"]["valor"] == 0.0 and e["dipolo_atl"]["valor"]["fecha"] == "2026-06-01"
    e2 = {x["id"]: x for x in indices.estado(tmp_path, date(2025, 12, 15))}
    assert e2["oni"]["valor"]["episodio"] == "La Niña" and "nota" not in e2["oni"]["valor"]
    assert e2["oni"]["valor"]["valor"] == -0.7 and "La Niña" in e2["oni"]["valor"]["fase"]
    assert e["pdo"]["valor"] is None and e["pdo"]["serie"] == []
    # Si después falla la fuente, queda el archivo anterior.
    indices.bajar(tmp_path, _Sesion({}))
    assert indices.leer(tmp_path, "sam")[0][-1][1] == 0.7


def test_condicion_sin_episodio(tmp_path):
    indices.guardar(tmp_path, "oni", [(date(2026, m, 1), v, {"trimestre": "x"}) for m, v in ((7, 0.2), (8, 0.55), (9, 0.7))])
    v = {x["id"]: x for x in indices.estado(tmp_path, date(2026, 10, 1), date(2026, 8, 1), date(2026, 10, 1))}["oni"]
    assert v["valor"]["fase"] == "condición Niño débil" and v["valor"]["episodio"] is None and "5 trimestres" in v["valor"]["nota"]
    assert v["periodo"]["fase"] == "condición Niño débil en promedio"


def test_api(cliente, tmp_path, monkeypatch):
    from meteo.web import app as web
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    indices.guardar(tmp_path, "sam", indices.leer_diario(SAM_DIARIO))
    d = cliente.get("/api/cartas/indices?fecha=2026-10-28&desde=2026-10-01&hasta=2026-10-28").json()
    sam = [x for x in d["indices"] if x["id"] == "sam"][0]
    assert sam["valor"]["fase"] == "positivo" and sam["periodo"]["n"] == 2 and len(sam["serie"]) == 2
    assert cliente.get("/api/cartas/indices?desde=2026-10-28&hasta=2026-10-01").status_code == 400


def test_api_un_indice(cliente, tmp_path, monkeypatch):
    from meteo.web import app as web
    monkeypatch.setattr(web, "ARCHIVO_DIR", str(tmp_path))
    indices.guardar(tmp_path, "sam", indices.leer_diario(SAM_DIARIO))
    d = cliente.get("/api/cartas/indices?fecha=2026-10-28&solo=sam&meses=600").json()
    assert [x["id"] for x in d["indices"]] == ["sam"] and len(d["indices"][0]["serie"]) == 2
    assert indices.INDICES["oni"].get("efecto") is None
