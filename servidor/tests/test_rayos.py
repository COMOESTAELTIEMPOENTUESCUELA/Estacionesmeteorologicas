"""Mapa de rayos (GLM): lectura de los destellos, disparador por tiempo
presente y armado del mapa solo cuando hay tormenta."""
import io
from datetime import datetime, timedelta, timezone

import h5netcdf
import numpy as np

UTC = timezone.utc


def archivo_lcfa(destellos, origen="2026-10-22 12:00:00.000"):
    """Un archivo como los del GLM: [(segundos desde el origen, lat, lon)].
    El tiempo va como entero de 16 bits SIN signo (_Unsigned) empaquetado:
    un valor grande queda negativo si se lo lee como entero con signo."""
    escala, offset = 0.00038147555, -5.0
    buf = io.BytesIO()
    with h5netcdf.File(buf, "w") as nc:
        nc.dimensions["number_of_flashes"] = len(destellos)
        lat = nc.create_variable("flash_lat", ("number_of_flashes",), "f4")
        lon = nc.create_variable("flash_lon", ("number_of_flashes",), "f4")
        t = nc.create_variable("flash_time_offset_of_first_event", ("number_of_flashes",), "i2")
        e = nc.create_variable("flash_energy", ("number_of_flashes",), "i2")
        lat[:] = [d[1] for d in destellos]
        lon[:] = [d[2] for d in destellos]
        crudo = np.round((np.array([d[0] for d in destellos]) - offset) / escala).astype("u2")
        t[:] = crudo.view("i2")
        t.attrs.update({"scale_factor": escala, "add_offset": offset, "_Unsigned": "true",
                        "units": f"seconds since {origen}"})
        e[:] = np.full(len(destellos), 1000, "i2")
        e.attrs.update({"scale_factor": 1.5e-15, "add_offset": 0.0, "_Unsigned": "true"})
    return buf.getvalue()


def test_leer_destellos_del_glm():
    from meteo.cartas import rayos
    datos = archivo_lcfa([(1.5, -34.9, -57.9),      # La Plata
                          (19.0, -35.0, -58.0),     # 19 s: como entero con signo daría negativo
                          (5.0, -20.0, -60.0)])     # fuera de la región
    lista = rayos.leer_destellos(datos, datetime(2026, 10, 22, 12, tzinfo=UTC), (-64, -53, -41.3, -30))
    assert len(lista) == 2
    (t1, la1, lo1, e1), (t2, *_ ) = lista
    assert abs((t1 - datetime(2026, 10, 22, 12, 0, 1, 500000, tzinfo=UTC)).total_seconds()) < 0.01
    assert abs((t2 - datetime(2026, 10, 22, 12, 0, 19, tzinfo=UTC)).total_seconds()) < 0.01
    assert abs(la1 + 34.9) < 1e-4 and abs(lo1 + 57.9) < 1e-4 and abs(e1 - 1.5e-12) < 1e-16
    assert rayos.leer_destellos(archivo_lcfa([]), datetime(2026, 10, 22, 12, tzinfo=UTC), (-64, -53, -41, -30)) == []


def test_listar_glm_del_bucket():
    from meteo.cartas import rayos

    class Sesion:
        def get(self, url, params=None, timeout=None):
            pre = params["prefix"]
            claves = [f"{pre}OR_GLM-L2-LCFA_G19_s2026295{h}{m}{s}0_e0_c0.nc"
                      for h in (pre.split("/")[3],) for m in ("00", "30", "59") for s in ("00", "20", "40")]
            xml = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                   + "".join(f"<Contents><Key>{c}</Key><Size>1</Size></Contents>" for c in claves)
                   + "<IsTruncated>false</IsTruncated></ListBucketResult>")
            return type("R", (), {"content": xml.encode(), "raise_for_status": lambda self: None})()

    t = datetime(2026, 10, 22, 11, tzinfo=UTC)
    l = rayos.listar_glm(t, t + timedelta(hours=1), Sesion())
    assert len(l) == 9 and l[0][0] == t and l[-1][0] == datetime(2026, 10, 22, 11, 59, 40, tzinfo=UTC)
    assert l[0][1].startswith("https://noaa-goes19.s3.amazonaws.com/GLM-L2-LCFA/2026/295/11/")


def test_mapa_solo_con_tormenta(tmp_path, monkeypatch):
    """Sin tormenta en el SYNOP no se baja nada; con tormenta (o --forzar) se
    arma el mapa y se guarda la lista de destellos."""
    from meteo.cartas import descarga, rayos
    pedidos = []
    monkeypatch.setattr(rayos, "destellos", lambda d, h, r, s: pedidos.append((d, h)) or
                        [(h - timedelta(minutes=5), -34.9, -57.9, 1e-12)])
    monkeypatch.setattr(rayos, "infrarrojo", lambda ts, r, s: "recorte")
    monkeypatch.setattr(rayos, "dibujar", lambda *a: b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    monkeypatch.setattr(descarga.archivo, "guardar_miniatura", lambda *a, **k: None, raising=False)
    p = {"id": "rayos", "fuente": "glm", "region": [-64, -53, -41.3, -30], "minutos": 60}
    desde, hasta = datetime(2026, 10, 20, tzinfo=UTC), datetime(2026, 10, 20, 18, tzinfo=UTC)
    vistos = []

    def tormenta(d, h, region):
        vistos.append((d, h))
        return ["La Plata Aero 95 (12 UTC)"] if d < datetime(2026, 10, 20, 12, tzinfo=UTC) <= h else []

    n = descarga._correr_rayos(p, tmp_path, None, (0, 6, 12, 18), tormenta, desde, hasta)
    assert n == 1                    # solo 12 UTC: la tormenta del SYNOP de las 12 cae en su ventana (11 a 13 UTC]
    assert vistos[2] == (datetime(2026, 10, 20, 11, tzinfo=UTC), datetime(2026, 10, 20, 13, tzinfo=UTC))
    assert pedidos[-1] == (datetime(2026, 10, 20, 11, tzinfo=UTC), datetime(2026, 10, 20, 12, tzinfo=UTC))
    assert descarga.archivo.existe(tmp_path, "rayos", datetime(2026, 10, 20, 12, tzinfo=UTC))
    assert not descarga.archivo.existe(tmp_path, "rayos", datetime(2026, 10, 20, 0, tzinfo=UTC))
    assert rayos.ruta_destellos(tmp_path, "rayos", datetime(2026, 10, 20, 12, tzinfo=UTC)).read_text().count("\n") == 2
    # Sin la base (sin disparador) no se hace nada; con forzar, todas las horas.
    assert descarga._correr_rayos(p, tmp_path, None, (0, 6, 12, 18), None, desde, hasta) == 0
    assert descarga._correr_rayos(p, tmp_path, None, (0, 6, 12, 18), None, desde, hasta, forzar=True) == 3  # 00, 06 y 18


def test_disparador_tormenta_en_el_synop():
    """Solo cuenta el tiempo presente de tormenta, de estaciones de la región y
    dentro de la ventana."""
    import os

    import pytest
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("sin TEST_DATABASE_URL")
    from meteo.ingesta import db
    from meteo.ingesta.base import Obs
    from meteo.ingesta.tareas import Ingesta
    t = datetime(2026, 7, 1, 18, tzinfo=UTC)
    conn = db.conectar(dsn)
    db.guardar_observaciones(conn, [Obs(t, "omm_87593", "ww", 95.0),                       # tormenta con lluvia
                                    Obs(t, "omm_87576", "ww", 61.0),                       # lluvia sin tormenta
                                    Obs(t + timedelta(hours=3), "omm_87585", "ww", 17.0)], "test")   # fuera de ventana
    try:
        ing = type("I", (), {"conn": conn})()
        region = (-64.0, -53.0, -41.3, -30.0)
        informes = Ingesta.hubo_tormenta(ing, t - timedelta(hours=1), t + timedelta(hours=1), region)
        assert informes == ["La Plata Aero 95 (18 UTC)"]
        # No deja la transacción abierta: después las cartas tardan ~25 min y la
        # base cortaría la conexión (idle_in_transaction_session_timeout).
        from psycopg.pq import TransactionStatus
        assert conn.info.transaction_status == TransactionStatus.IDLE
        assert Ingesta.hubo_tormenta(ing, t - timedelta(hours=1), t + timedelta(hours=1), (-70, -65, -50, -45)) == []
    finally:
        conn.execute("DELETE FROM observacion WHERE variable = 'ww' AND ts BETWEEN %s AND %s",
                     (t - timedelta(hours=1), t + timedelta(hours=4)))
        conn.commit()
        conn.close()
