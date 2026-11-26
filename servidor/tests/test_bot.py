"""El bot observador: qué detecta y que no repita alertas."""
from datetime import datetime, timedelta, timezone

from meteo.ingesta import bot

AHORA = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)
h = lambda x: AHORA - timedelta(hours=x)


def test_detectar_cambios_bruscos():
    e = {"id": "omm_87593", "nombre": "La Plata Aero", "tipo": "sinoptica",
         "temp": [(h(1), 27.0), (h(0), 22.4)], "pres": [(h(3), 1012.1), (h(0), 1008.7)], "racha": [], "ww": [(h(0), 95)]}
    f = bot.detectar(e, AHORA)
    assert len(f) == 3 and "bajó 3,4 hPa en 3 h" in f[0] and "bajó 4,6 °C en 1 h" in f[1] and "tormenta" in f[2]
    tranquilo = {**e, "temp": [(h(1), 25.0), (h(0), 23.0)], "pres": [(h(3), 1012.0), (h(0), 1010.0)], "ww": [(h(0), 2)]}
    assert bot.detectar(tranquilo, AHORA) == []
    # Una EMA de escuela: solo cuenta la ráfaga (17 m/s = 61 km/h), no el salto de temperatura.
    ema = {"id": "ema_x", "nombre": "EMA Escuela", "tipo": "ema", "temp": [(h(1), 30.0), (h(0), 20.0)], "pres": [], "racha": [(h(0.5), 17.0)], "ww": []}
    assert bot.detectar(ema, AHORA) == ["ráfaga de 61 km/h a las 14:30"]
    # Datos viejos (de hace 3 h) no disparan nada.
    viejo = {**e, "temp": [(h(4), 27.0), (h(3), 20.0)], "pres": [(h(6), 1015.0), (h(3), 1008.0)], "ww": []}
    assert bot.detectar(viejo, AHORA) == []


def test_imagen_segun_lo_detectado(tmp_path):
    """Tormenta -> rayos del GLM (o infrarrojo); frente -> carta de análisis.
    La imagen más cercana al aviso que ya esté archivada."""
    from meteo.cartas import archivo
    assert bot.imagenes_para(["el SYNOP informa tormenta (a las 15:00)", "la presión bajó 3,4 hPa en 3 h (…)"])[:3] == \
        ["satelite_rayos_bsas", "satelite_glm", "satelite_ir13"]
    assert bot.imagenes_para(["la temperatura bajó 4,6 °C en 1 h (…)"])[0] == "gfs_ana_mslp_jet"
    assert bot.imagenes_para(["ráfaga de 61 km/h a las 14:30"])[0] == "satelite_rayos_bsas"
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    alerta = datetime(2026, 11, 5, 16, 40, tzinfo=timezone.utc)
    lista = bot.imagenes_para(["el SYNOP informa tormenta (a las 13:40)"])
    assert bot.elegir_imagen(tmp_path, lista, alerta) is None                         # todavía no llegó nada
    archivo.guardar(tmp_path, "satelite_ir13", datetime(2026, 11, 5, 18, tzinfo=timezone.utc), png)
    assert bot.elegir_imagen(tmp_path, lista, alerta) == ("satelite_ir13", datetime(2026, 11, 5, 18, tzinfo=timezone.utc))
    archivo.guardar(tmp_path, "satelite_rayos_bsas", datetime(2026, 11, 5, 12, tzinfo=timezone.utc), png)   # a 4 h 40: muy lejos
    assert bot.elegir_imagen(tmp_path, lista, alerta)[0] == "satelite_ir13"
    archivo.guardar(tmp_path, "satelite_rayos_bsas", datetime(2026, 11, 5, 18, tzinfo=timezone.utc), png)
    assert bot.elegir_imagen(tmp_path, lista, alerta) == ("satelite_rayos_bsas", datetime(2026, 11, 5, 18, tzinfo=timezone.utc))


def test_publica_una_vez(cliente, tmp_path, monkeypatch):
    """Contra la base: publica la nota y no repite la misma estación en 3 h."""
    from meteo.web.app import pool_escritura
    raiz = tmp_path
    monkeypatch.setenv("ARCHIVO_DIR", str(raiz))
    with pool_escritura.connection() as conn:
        conn.execute("""INSERT INTO estacion (id, nombre, tipo, fuente) VALUES ('test_bot', 'Estación Bot', 'sinoptica', 'manual')
                        ON CONFLICT (id) DO NOTHING""")
        conn.execute("DELETE FROM observacion WHERE estacion_id = 'test_bot'")
        for ts, v in ((h(1), 26.0), (h(0), 21.5)):
            conn.execute("INSERT INTO observacion (ts, estacion_id, variable, valor) VALUES (%s, 'test_bot', 'temp', %s)", (ts, v))
        conn.commit()
        try:
            n1 = bot.correr(conn, AHORA)
            assert n1
            r = conn.execute("SELECT texto, etiquetas, contexto FROM nota WHERE id = %s", (n1,)).fetchone()
            texto, etq, ctx = r["texto"], r["etiquetas"], r["contexto"]
            assert "Estación Bot: la temperatura bajó 4,5 °C" in texto and etq == ["alertanowcasting"] and ctx["estaciones"] == "test_bot"
            assert ctx["imagenes"][0] == "gfs_ana_mslp_jet" and "producto" not in ctx     # la carta todavía no llegó
            # Es "Bot Zari", con su foto de zarigüeya (la misma que el archivo del dibujo).
            u = conn.execute("""SELECT u.nombre, f.datos FROM usuario u LEFT JOIN usuario_foto f ON f.usuario_id = u.id
                                WHERE u.email = %s""", (bot.EMAIL_BOT,)).fetchone()
            assert u["nombre"] == "Bot Zari" and bytes(u["datos"]) == bot.FOTO_BOT.read_bytes()
            assert bot.correr(conn, AHORA + timedelta(minutes=15)) is None        # silencio de 3 h
            # Llega la carta de análisis: en la vuelta siguiente la nota pasa a mostrarla.
            from meteo.cartas import archivo
            archivo.guardar(raiz, "gfs_ana_mslp_jet", AHORA, b"\x89PNG\r\n\x1a\n" + b"0" * 50)
            bot.correr(conn, AHORA + timedelta(minutes=30))
            ctx = conn.execute("SELECT contexto FROM nota WHERE id = %s", (n1,)).fetchone()["contexto"]
            assert (ctx["seccion"], ctx["producto"], ctx["ts"][:13]) == ("analisis", "gfs_ana_mslp_jet", "2026-11-05T18")
        finally:
            conn.execute("DELETE FROM nota WHERE usuario_id IN (SELECT id FROM usuario WHERE email = %s)", (bot.EMAIL_BOT,))
            conn.execute("DELETE FROM observacion WHERE estacion_id = 'test_bot'")
            conn.execute("DELETE FROM estacion WHERE id = 'test_bot'")
            conn.commit()
