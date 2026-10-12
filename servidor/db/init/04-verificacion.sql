-- Verificación de pronósticos: lo observado cada día en cada ciudad, en los
-- mismos términos que el pronóstico (ver docs/05-verificacion.md).
-- Lo calcula la tarea "verificacion" del servicio de ingesta.
-- Se puede correr de nuevo sin problemas (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS verif_observado (
    ciudad      text NOT NULL,              -- como en el pronóstico: 'La Plata'
    fecha       date NOT NULL,              -- día civil, hora argentina
    elemento    text NOT NULL,              -- tmax_urbana, tmin_suburbana, pp, viento_dir, viento_int...
    valor       double precision,           -- °C, 1/0, km/h
    categoria   text,                       -- SI/NO, sector (SE), intensidad (LEVES), período de la mínima
    estacion_id text REFERENCES estacion(id),
    detalle     jsonb,                      -- completitud, valores por período, evidencias...
    calculado   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ciudad, fecha, elemento)
);
