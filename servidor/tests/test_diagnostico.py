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


def test_pronostico_gfs_todos_los_plazos_de_la_ultima_corrida(tmp_path, monkeypatch):
    """Pronóstico dibujado en el servidor (formato Bentley): la corrida más
    nueva que ya está completa en NOAA, todos los plazos, y solo la última."""
    from meteo.cartas import descarga, estilo_bentley as eb, gfs, pronostico
    monkeypatch.setattr(descarga, "REINTENTOS_SEG", (0, 0))
    publicadas = {datetime(2026, 10, 29, 6, tzinfo=UTC)}      # la de las 12 todavía no llegó a +192 h
    bajadas, falla = [], {"plazo": None}

    class Sesion:
        def get(self, url, timeout):
            ts = datetime.strptime(url.split("gfs.")[1][:8] + url.split("/")[4], "%Y%m%d%H").replace(tzinfo=UTC)
            return type("R", (), {"status_code": 200 if ts in publicadas else 404})()

    def bajar(ts, pedidos, region, sesion, plazo=0):
        if plazo == falla["plazo"]:
            raise ConnectionError("cortado")
        bajadas.append((ts, plazo))
        return {"plazo": plazo}

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    monkeypatch.setattr(gfs, "bajar", bajar)
    monkeypatch.setattr(eb, "dibujar", lambda campos, inicio, plazo: {k: png + bytes([plazo]) for k in eb.CARTAS})
    monkeypatch.setattr(eb, "marco", lambda: {"caja": [0.1, 0.05, 0.9, 0.85], "x": [0, 1], "y": [0, 1]})
    productos = [{"id": f"pro_{k}", "fuente": "gfs_pronostico", "carta": k} for k in eb.CARTAS]
    analisis = [{"id": "ana_ivt", "fuente": "gfs_analisis", "carta": "ivt"}]
    ahora = datetime(2026, 10, 29, 16, 30, tzinfo=UTC)
    assert descarga._correr_pronostico_gfs(productos, tmp_path, Sesion(), ahora, analisis=analisis) == 33 * 4
    # El +0 h queda archivado para siempre como carta de análisis de esa hora.
    assert descarga.archivo.existe(tmp_path, "ana_ivt", datetime(2026, 10, 29, 6, tzinfo=UTC))
    d = pronostico.leer(tmp_path, "pro_ivt")
    assert d["inicio"] == "2026-10-29T06:00:00+00:00" and len(d["cuadros"]) == 33
    assert d["cuadros"][-1]["plazo_h"] == 192 and d["cuadros"][-1]["valida"] == "2026-11-06T06:00:00+00:00"
    assert len(bajadas) == 33                                    # un archivo por plazo para las cuatro
    bajadas.clear()
    assert descarga._correr_pronostico_gfs(productos, tmp_path, Sesion(), ahora) == 0 and not bajadas
    # Sale la de las 12, pero un plazo falla siempre: queda la de las 06 entera.
    publicadas.add(datetime(2026, 10, 29, 12, tzinfo=UTC))
    falla["plazo"] = 96
    with pytest.raises(ConnectionError):
        descarga._correr_pronostico_gfs(productos, tmp_path, Sesion(), ahora)
    assert pronostico.leer(tmp_path, "pro_ivt")["inicio"] == "2026-10-29T06:00:00+00:00"
    falla["plazo"] = None
    assert descarga._correr_pronostico_gfs(productos, tmp_path, Sesion(), ahora) == 33 * 4
    # La nueva es la última; la anterior queda en el historial (8 días).
    assert [d["inicio"][:13] for d in pronostico.corridas(tmp_path, "pro_ivt")] == ["2026-10-29T12", "2026-10-29T06"]
    assert pronostico.leer(tmp_path, "pro_ivt")["marco"]["caja"] == [0.1, 0.05, 0.9, 0.85]
    archivos = [f.name for f in pronostico.carpeta(tmp_path, "pro_ivt").glob("*.png")]
    assert len(archivos) == 66


def test_estilo_bentley_dibuja_las_cuatro():
    """Con campos inventados (pero razonables) salen las cuatro imágenes."""
    from meteo.cartas import estilo_bentley as eb
    from meteo.cartas.gfs import Campo
    lat = np.arange(25, -80.1, -0.5)
    lon = np.arange(-150, 40.1, 0.5)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    onda = np.sin(np.radians(lo * 3)) * np.cos(np.radians(la * 2))
    valores = {"PRMSL": 101300 + 1500 * onda, "PRES": np.full(la.shape, 100000.0), "TMP": 280 + 10 * onda,
               "RH": 60 + 30 * onda, "ABSV": 1e-4 * onda, "VVEL": -1.0 * onda, "SPFH": 0.008 + 0.004 * onda,
               "UGRD": 20 + 30 * onda, "VGRD": 10 * onda}
    alturas = {"1000 mb": 100, "850 mb": 1450, "700 mb": 3000, "500 mb": 5600}
    campos = {}
    for var, nivel in eb.PEDIDOS:
        v = (alturas[nivel] + 60 * onda) if var == "HGT" else valores[var]
        campos[(var, nivel)] = Campo(lat, lon, np.asarray(v, float))
    out = eb.dibujar(campos, datetime(2026, 10, 29, 6, tzinfo=UTC), 48)
    assert set(out) == set(eb.CARTAS) and all(png[:4] == b"\x89PNG" for png in out.values())


def test_marco_ubica_las_ciudades_donde_las_pone_cartopy():
    """La cuenta de a_imagen (la misma que hace la página en JavaScript para
    los puntos de las ciudades) coincide con la proyección de las cartas."""
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt
    from meteo.cartas import estilo_bentley as eb
    m = eb.marco()
    fig, ax = eb._marco("", datetime(2026, 1, 1, tzinfo=UTC), 0)
    fig.canvas.draw()
    for lon, lat in [(-57.95, -34.92), (-62.27, -38.72), (-120, 10), (0, -60)]:
        x, y = ax.projection.transform_point(lon, lat, ccrs.PlateCarree())
        px, py = ax.transData.transform((x, y))
        esperado = (px / fig.bbox.width, 1 - py / fig.bbox.height)
        fx, fy = eb.a_imagen(m, lon, lat)
        assert abs(fx - esperado[0]) < 1e-4 and abs(fy - esperado[1]) < 1e-4, (lon, lat)
    plt.close(fig)


def test_historial_de_corridas(tmp_path):
    """Con conservar: las corridas de los últimos días quedan (sus imágenes y
    su corrida.json en corridas/); las más viejas se borran."""
    from meteo.cartas import pronostico
    def publicar(ini, ahora):
        tmp = pronostico.empezar(tmp_path, "g")
        nom = pronostico.nombre("g", ini, 6)
        (tmp / nom).write_bytes(b"x")
        pronostico.publicar(tmp_path, "g", tmp, ini, [(6, ini + timedelta(hours=6), nom)],
                            conservar=timedelta(days=8), ahora=ahora, extra={"marco": {"caja": [0, 0, 1, 1]}})
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for h in range(0, 24 * 10, 6):                                  # 10 días de corridas
        publicar(base + timedelta(hours=h), base + timedelta(hours=h + 5))
    todas = pronostico.corridas(tmp_path, "g")
    inicios = [datetime.fromisoformat(d["inicio"]) for d in todas]
    assert inicios[0] == base + timedelta(hours=234) and inicios == sorted(inicios, reverse=True)
    assert min(inicios) >= base + timedelta(hours=239) - timedelta(days=8)   # lo de más de 8 días, borrado
    archivos = list(pronostico.carpeta(tmp_path, "g").glob("*.png"))
    assert len(archivos) == len(todas) == 32                          # 8 días x 4 corridas, una imagen por corrida
    assert todas[0]["marco"] == {"caja": [0, 0, 1, 1]}


def test_analisis_propio_formato_bentley(tmp_path, monkeypatch):
    """Las cartas de análisis propias (fuente gfs_analisis) se dibujan con el
    formato del pronóstico (estilo_bentley, plazo 0), una por hora sinóptica."""
    from meteo.cartas import descarga, estilo_bentley as eb, gfs
    vistos = []
    monkeypatch.setattr(gfs, "bajar", lambda ts, pedidos, region, sesion: (vistos.append((pedidos, region)) or {"ts": ts}))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    plazos = []
    monkeypatch.setattr(eb, "dibujar", lambda campos, ts, plazo: (plazos.append(plazo) or {k: png for k in eb.CARTAS}))
    productos = [{"id": f"ana_{k}", "fuente": "gfs_analisis", "carta": k} for k in eb.CARTAS]
    desde, hasta = datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 26, 12, tzinfo=UTC)
    assert descarga._correr_gfs(productos, tmp_path, None, (0, 6, 12, 18), desde, hasta) == 12
    from meteo.cartas import puntos
    assert vistos[0] == (eb.PEDIDOS | puntos.PEDIDOS, eb.REGION) and set(plazos) == {0}
    assert descarga.archivo.existe(tmp_path, "ana_500_vort", datetime(2026, 10, 26, 6, tzinfo=UTC))
    # Además, los valores en las ciudades de cada hora.
    assert puntos.existe(tmp_path, datetime(2026, 10, 26, 12, tzinfo=UTC))
    # Si las cartas ya están pero faltan los valores (fechas viejas), se bajan solo para eso.
    puntos.ruta(tmp_path, datetime(2026, 10, 26, 6, tzinfo=UTC)).unlink()
    plazos.clear()
    assert descarga._correr_gfs(productos, tmp_path, None, (0, 6, 12, 18), desde, hasta) == 0
    assert puntos.existe(tmp_path, datetime(2026, 10, 26, 6, tzinfo=UTC)) and not plazos   # sin volver a dibujar


def test_valores_del_gfs_en_una_ciudad():
    """Interpolación bilineal, viento, rocío, IVT y perfil (sin los niveles
    bajo el suelo) en un punto."""
    from meteo.cartas import puntos
    from meteo.cartas.gfs import Campo
    lat = np.arange(-30, -40.1, -0.5)
    lon = np.arange(-62, -54.9, 0.5)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    def campo(v):
        return Campo(lat, lon, np.broadcast_to(np.asarray(v, float), la.shape).copy())
    campos = {("PRMSL", "mean sea level"): campo(101000 + 100 * (lo + 60)),     # sube 1 hPa por grado al este
              ("PRES", "surface"): campo(98000.0), ("HGT", "surface"): campo(300.0)}
    for n in puntos.NIVELES_PERFIL:
        campos[("TMP", f"{n} mb")] = campo(273.15 + 15 - (1000 - n) * 0.06)
        campos[("RH", f"{n} mb")] = campo(50.0)
        campos[("HGT", f"{n} mb")] = campo((1000 - n) * 10.0)
        campos[("UGRD", f"{n} mb")] = campo(-10.0)                           # viento del este
        campos[("VGRD", f"{n} mb")] = campo(0.0)
    for n in (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300):
        campos[("SPFH", f"{n} mb")] = campo(0.008)
    r = puntos.extraer(campos, {"X": (-34.92, -57.95)})["X"]
    assert abs(r["pnm"] - (1010 + (-57.95 + 60))) < 0.06                       # interpolado entre grillas (redondeado a 0,1)
    assert r["v850"] == [round(10 * 1.943844, 1), 90]                          # 19,4 kt del este
    assert r["perfil"]["p"][0] == 975 and 1000 not in r["perfil"]["p"]         # 1000 hPa está bajo el suelo (980)
    assert abs(r["td850"] - puntos.rocio(6.0, 50)) < 0.05
    # IVT = q·|V|·Δp/g = 0,008 x 10 x (975-300) hPa / g, hacia el oeste (270°).
    assert abs(r["ivt"][0] - 0.008 * 10 * 67500 / 9.80665) < 2 and r["ivt"][1] == 270


def _igra(ts, niveles, estacion="ARM00087576"):
    """Un sondeo en formato IGRA2 (columnas fijas, como lo publica NOAA)."""
    lineas = [f"#{estacion:11s} {ts.year:4d} {ts.month:02d} {ts.day:02d} {ts.hour:02d} 1115 {len(niveles):4d} ncdc-gts ncdc-gts -348200 -585300"]
    for p, z, t, dpd, wd, ws in niveles:
        lineas.append(f"21 {-9999:5d} {p:6d}B{z:5d}B{t:5d}B{-9999:5d} {dpd:5d} {wd:5d} {ws:5d}")
    return "\n".join(lineas)


def test_leer_igra():
    """Formato IGRA2: Pa, décimas de °C y de m/s, -9999 = falta; niveles de
    abajo hacia arriba y sin presiones repetidas."""
    from meteo.cartas import sondeos
    t = datetime(2026, 10, 29, 12, tzinfo=UTC)
    texto = _igra(t, [(101800, 20, 158, 46, 20, 36), (100000, 170, 150, 50, 30, 50), (85000, 1520, 60, 40, 270, 100),
                      (85000, 1520, 60, 40, 270, 100), (70000, 3100, -20, 100, 280, 150), (50000, 5700, -190, 200, 285, 250),
                      (40000, 7300, -300, -9999, 290, 300), (30000, 9300, -450, -9999, 290, -9999)])
    texto += "\n" + _igra(datetime(2026, 10, 30, 0, tzinfo=UTC), [(100000, 100, 100, 10, 0, 0)])   # muy corto: se descarta
    [s] = sondeos.leer_igra(texto)
    assert s["ts"] == t and [n["p"] for n in s["niveles"]] == [1018, 1000, 850, 700, 500, 400, 300]
    assert s["niveles"][0] == {"p": 1018, "z": 20, "t": 15.8, "td": 11.2, "dir": 20, "kt": 7.0}
    assert s["niveles"][5]["td"] is None and s["niveles"][6]["kt"] is None
    assert sondeos.nombre_archivo_igra('<a href="ARM00087576-data-beg2025.txt.zip">', "ARM00087576") == "ARM00087576-data-beg2025.txt.zip"


def test_indices_de_un_sondeo():
    """Comparados con MetPy para un perfil real del GFS (La Plata, 29/10/2026
    18 UTC): PW 22,3 mm, NCL 890 hPa, CAPE 422 J/kg, K 24,1, LI -2,2."""
    from meteo.cartas import sondeos
    p = [1014.3, 1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100]
    t = [21.2, 19.1, 17.2, 15.2, 13.2, 11.1, 7.3, 3.8, 1.0, -1.5, -4.5, -8.9, -13.7, -19.3, -24.6, -30.7, -36.9, -43.7, -48.4, -49.4, -52.6, -58.1]
    td = [12.4, 10.7, 10.1, 9.5, 8.9, 8.2, 6.1, 1.9, -5.1, -10.1, -17.2, -25.9, -31.2, -44.5, -48.7, -52.8, -55.0, -58.7, -64.0, -74.7, -80.9, -85.6]
    niveles = [{"p": a, "z": None, "t": b, "td": c} for a, b, c in zip(p, t, td)]
    r = sondeos.indices(niveles)
    assert abs(r["pw"] - 22.3) < 0.3 and abs(r["ncl"] - 890) < 2
    assert abs(r["cape"] - 422) < 25 and r["cin"] == 0 and r["k"] == 24.1 and abs(r["li"] + 2.2) < 0.5
    assert len(r["parcela"]) == len(p)
    # Parcela: seca hasta el NCL (conserva θ) y después más tibia que la seca.
    assert abs(r["parcela"][1] - ((21.2 + 273.15) * (1000 / 1014.3) ** (287.04 / 1005.7) - 273.15)) < 0.2


def test_bajar_radiosondeos_de_igra(tmp_path):
    """Cada 6 h se baja el archivo del año de cada estación y se guardan los
    sondeos que falten."""
    import io
    import zipfile
    from meteo.cartas import descarga, sondeos
    t = datetime(2026, 10, 29, 12, tzinfo=UTC)
    niv = [(101800, 20, 158, 46, 20, 36), (100000, 170, 150, 50, 30, 50), (85000, 1520, 60, 40, 270, 100),
           (70000, 3100, -20, 100, 280, 150), (50000, 5700, -190, 200, 285, 250)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ARM00087576-data-beg2025.txt", _igra(t, niv))
    pedidos = []

    class Sesion:
        def get(self, url, timeout):
            pedidos.append(url)
            cuerpo = buf.getvalue() if url.endswith(".zip") else b'<a href="ARM00087576-data-beg2025.txt.zip">'
            return type("R", (), {"content": cuerpo, "text": cuerpo.decode("latin-1"), "raise_for_status": lambda self: None})()

    est = [{"id": "87576", "igra": "ARM00087576", "nombre": "Ezeiza"}]
    assert descarga._correr_sondeos(est, tmp_path, Sesion(), ahora=t) == 1
    assert sondeos.leer(tmp_path, "87576", t)["niveles"][2]["p"] == 850
    pedidos.clear()
    assert descarga._correr_sondeos(est, tmp_path, Sesion(), ahora=t + timedelta(hours=2)) == 0 and not pedidos  # antes de 6 h
    assert descarga._correr_sondeos(est, tmp_path, Sesion(), ahora=t + timedelta(hours=7)) == 0 and pedidos     # ya estaba
