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


def test_publica_una_vez(cliente):
    """Contra la base: publica la nota y no repite la misma estación en 3 h."""
    from meteo.web.app import pool_escritura
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
            assert bot.correr(conn, AHORA + timedelta(minutes=15)) is None        # silencio de 3 h
        finally:
            conn.execute("DELETE FROM nota WHERE usuario_id IN (SELECT id FROM usuario WHERE email = %s)", (bot.EMAIL_BOT,))
            conn.execute("DELETE FROM observacion WHERE estacion_id = 'test_bot'")
            conn.execute("DELETE FROM estacion WHERE id = 'test_bot'")
            conn.commit()
