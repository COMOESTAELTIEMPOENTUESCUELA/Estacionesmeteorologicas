-- Bitácora de las estaciones: por qué se cayó una EMA (internet, luz...) y
-- cuándo se le hizo mantenimiento. Los CORTES se detectan solos (huecos en
-- los datos); acá se anota la CAUSA, que la base no puede adivinar.
-- La web la crea sola al arrancar si no existe (IF NOT EXISTS): no hace falta
-- correrlo a mano. Ver docs/02-datos.md.
CREATE TABLE IF NOT EXISTS estacion_incidente (
    id          serial PRIMARY KEY,
    estacion_id text REFERENCES estacion(id),   -- NULL = toda la red (ej. se cayó el servidor)
    causa       text NOT NULL,                  -- internet, energia, sensor, mantenimiento... (lista en app.py)
    desde       timestamptz NOT NULL,
    hasta       timestamptz,                    -- NULL = todavía sigue
    datos       text NOT NULL DEFAULT 'sin_datos'
                CHECK (datos IN ('sin_datos', 'dudosos', 'normales')),   -- qué pasó con los datos mientras tanto
    descripcion text,
    autor       text,
    creado      timestamptz NOT NULL DEFAULT now(),
    actualizado timestamptz NOT NULL DEFAULT now(),
    CHECK (hasta IS NULL OR hasta > desde)
);
CREATE INDEX IF NOT EXISTS estacion_incidente_desde ON estacion_incidente (estacion_id, desde);
