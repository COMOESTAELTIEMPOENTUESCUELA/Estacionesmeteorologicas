-- =====================================================================
-- Esquema de la base de datos meteorológica.
--
-- Postgres ejecuta este archivo automáticamente la PRIMERA vez que se crea
-- la base (carpeta /docker-entrypoint-initdb.d). Para cambios posteriores se
-- agregan archivos nuevos numerados (02-..., 03-...) y se aplican a mano.
--
-- Ideas clave:
-- * Todo instante se guarda como timestamptz (en UTC por dentro). La hora
--   local se calcula al consultar. Nunca se mezclan husos.
-- * Las observaciones van en formato "largo": una fila por
--   (instante, estación, variable). Agregar una variable o estación nueva no
--   requiere cambiar tablas.
-- * Nada se borra por "dato malo": se marca con una bandera de calidad (qc).
-- * Los metadatos (qué instrumento, desde cuándo, qué tipo de estación) son
--   tan importantes como los datos.
-- =====================================================================

-- TimescaleDB es opcional: si la extensión está (imagen de Docker
-- timescale/timescaledb) se usa; si no, todo funciona igual en Postgres común.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB no disponible: se usa Postgres común';
END $$;

-- ---------------------------------------------------------------------
-- Catálogo de variables: nombre, unidad y límites físicos (control de calidad)
-- ---------------------------------------------------------------------
CREATE TABLE variable (
    id           text PRIMARY KEY,
    nombre       text NOT NULL,
    unidad       text NOT NULL,
    descripcion  text,
    min_fisico   double precision,   -- por debajo: dato "malo" (qc=4)
    max_fisico   double precision
);

INSERT INTO variable (id, nombre, unidad, descripcion, min_fisico, max_fisico) VALUES
 ('temp',          'Temperatura del aire',          '°C',   'Bulbo seco / sensor de temperatura a ~1,5 m', -30, 50),
 ('tbh',           'Temperatura de bulbo húmedo',   '°C',   'Psicrómetro (estación convencional)', -30, 45),
 ('tmax',          'Temperatura máxima',            '°C',   'Extremo leído; el período depende de la fuente (ver estacion.notas)', -25, 50),
 ('tmin',          'Temperatura mínima',            '°C',   'Extremo leído; el período depende de la fuente', -30, 40),
 ('td',            'Temperatura de rocío',          '°C',   NULL, -40, 35),
 ('hum',           'Humedad relativa',              '%',    NULL, 1, 100.5),
 ('tension_vapor', 'Tensión de vapor',              'hPa',  NULL, 0, 60),
 ('pres_est',      'Presión a nivel de estación',   'hPa',  NULL, 850, 1080),
 ('pnm',           'Presión a nivel del mar',       'hPa',  NULL, 900, 1080),
 ('barometro_mmhg','Lectura cruda del barómetro',   'mmHg', 'Sin corregir (convencional)', 650, 810),
 ('temp_adjunto',  'Termómetro adjunto al barómetro','°C',  NULL, -10, 50),
 ('precip',        'Precipitación',                 'mm',   'Acumulada en el intervalo que TERMINA en ts (desde el registro anterior)', 0, 250),
 ('precip_1h',     'Precipitación 1 h',             'mm',   'SYNOP: acumulada en la hora que termina en ts', 0, 150),
 ('precip_3h',     'Precipitación 3 h',             'mm',   'SYNOP', 0, 250),
 ('precip_6h',     'Precipitación 6 h',             'mm',   'SYNOP', 0, 300),
 ('precip_12h',    'Precipitación 12 h',            'mm',   'SYNOP', 0, 400),
 ('precip_24h',    'Precipitación 24 h',            'mm',   'SYNOP', 0, 500),
 ('intens_precip', 'Intensidad de precipitación',   'mm/h', NULL, 0, 500),
 ('viento_vel',    'Velocidad del viento',          'm/s',  'Media del intervalo / 10 min', 0, 75),
 ('viento_dir',    'Dirección del viento',          '°',    'De dónde viene; 0 = calma', 0, 360),
 ('racha',         'Ráfaga',                        'm/s',  NULL, 0, 90),
 ('rad_solar',     'Radiación solar global',        'W/m²', NULL, 0, 1500),
 ('nubosidad',     'Nubosidad total',               'octas','9 = cielo invisible', 0, 9),
 ('visibilidad',   'Visibilidad',                   'm',    NULL, 0, 100000),
 ('ww',            'Tiempo presente (código ww)',   'código','Tabla 4677 de la OMM', 0, 99);

-- ---------------------------------------------------------------------
-- Estaciones y sus metadatos
-- ---------------------------------------------------------------------
CREATE TABLE estacion (
    id           text PRIMARY KEY,           -- ej. 'bavio', 'omm_87593'
    nombre       text NOT NULL,
    tipo         text NOT NULL CHECK (tipo IN
                   ('ema', 'convencional', 'sinoptica', 'pluviometro', 'otra')),
    red          text,                        -- 'escuelas', 'fcag', 'smn', ...
    fuente       text NOT NULL,               -- thingspeak | wunderground | davis | ogimet | manual
    institucion  text,
    localidad    text,
    lat          double precision,
    lon          double precision,
    elevacion_m  double precision,
    omm          text,                        -- indicativo OMM si es sinóptica
    activa       boolean NOT NULL DEFAULT true,
    alta         date,
    baja         date,
    notas        text,
    config       jsonb NOT NULL DEFAULT '{}'  -- cómo leer la fuente (canal, campos...)
);

-- Historial de instrumentos: si se cambia un sensor o se mueve el
-- pluviómetro, se cierra el registro viejo (hasta) y se abre uno nuevo.
-- Imprescindible para estudios de homogeneidad de series.
CREATE TABLE instrumento (
    id           serial PRIMARY KEY,
    estacion_id  text NOT NULL REFERENCES estacion(id),
    variable     text REFERENCES variable(id),
    tipo         text NOT NULL,   -- pluviometro_balancin | pluviometro_convencional | termometro_bulbo | sensor_digital ...
    marca_modelo text,
    resolucion   text,            -- ej. '0,2 mm por pulso'
    altura_m     double precision,
    desde        date,
    hasta        date,
    notas        text
);

-- ---------------------------------------------------------------------
-- Observaciones (la tabla grande)
-- ---------------------------------------------------------------------
-- qc: 0 = sin controlar, 1 = pasó los controles, 3 = sospechoso, 4 = malo
CREATE TABLE observacion (
    ts           timestamptz      NOT NULL,
    estacion_id  text             NOT NULL REFERENCES estacion(id),
    variable     text             NOT NULL REFERENCES variable(id),
    valor        double precision NOT NULL,
    qc           smallint         NOT NULL DEFAULT 0,
    fuente       text,                       -- qué recolector lo trajo
    ingestado    timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (estacion_id, variable, ts)
);
CREATE INDEX observacion_ts ON observacion (ts DESC);

-- Mensajes SYNOP tal cual llegaron: si mejora el decodificador, se reprocesa.
CREATE TABLE synop_crudo (
    estacion_id  text        NOT NULL REFERENCES estacion(id),
    ts           timestamptz NOT NULL,
    mensaje      text        NOT NULL,
    ingestado    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (estacion_id, ts)
);

-- Con TimescaleDB: la tabla se parte en "chunks" por tiempo y lo viejo se
-- comprime (suele ocupar 10-20 veces menos).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable('observacion', 'ts', chunk_time_interval => interval '30 days');
        ALTER TABLE observacion SET (timescaledb.compress,
            timescaledb.compress_segmentby = 'estacion_id, variable');
        PERFORM add_compression_policy('observacion', interval '60 days');
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Reportes de la comunidad (ciencia ciudadana)
-- ---------------------------------------------------------------------
CREATE TABLE reporte_comunidad (
    id             bigserial PRIMARY KEY,
    ts             timestamptz NOT NULL,      -- cuándo se envió el reporte
    tipo           text NOT NULL,             -- lluvia | rocio | escarcha | granizo | niebla | otro
    escuela        text,
    reportero      text,
    lluvia_mm      double precision,
    -- Metadato clave para comparar con las estaciones:
    instrumento    text NOT NULL DEFAULT 'sin_dato' CHECK (instrumento IN
                     ('pluviometro_convencional', 'pluviometro_casero', 'estacion_automatica',
                      'estimacion', 'sin_dato')),
    periodo        text NOT NULL DEFAULT 'sin_dato' CHECK (periodo IN
                     ('24h_9hs', 'desde_ultima_lectura', 'evento', 'sin_dato')),
    estacion_id    text REFERENCES estacion(id),  -- estación más cercana / de esa escuela
    estado_camino  text,
    resumen        text,
    imagen_url     text,
    lat            double precision,
    lon            double precision,
    qc             smallint NOT NULL DEFAULT 0,
    huella         text NOT NULL UNIQUE,      -- evita duplicados al re-leer la planilla
    ingestado      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reporte_comunidad_ts ON reporte_comunidad (ts DESC);

-- ---------------------------------------------------------------------
-- Pronósticos emitidos (Pronóstico UNLP), para verificarlos después
-- ---------------------------------------------------------------------
CREATE TABLE pronostico_emision (
    id               bigserial PRIMARY KEY,
    ciudad           text NOT NULL,
    fecha_pronostico date NOT NULL,
    hora_emision     text,
    publicado_por    text,
    es_correccion    boolean,
    contenido        jsonb NOT NULL,          -- el JSON completo, tal cual
    huella           text NOT NULL UNIQUE,
    capturado        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pronostico_dia (
    emision_id      bigint NOT NULL REFERENCES pronostico_emision(id) ON DELETE CASCADE,
    fecha           date   NOT NULL,          -- día pronosticado
    plazo_dias      int    NOT NULL,          -- 0 = mismo día de la emisión, 1 = mañana...
    temp_min        double precision,
    temp_max        double precision,
    temp_min_suburbana double precision,
    temp_max_suburbana double precision,
    periodos        jsonb,                    -- mañana/tarde/noche con cielo, viento, prob. lluvia
    PRIMARY KEY (emision_id, fecha)
);

-- ---------------------------------------------------------------------
-- Eventos significativos (para el análisis mensual)
-- ---------------------------------------------------------------------
CREATE TABLE evento (
    id           serial PRIMARY KEY,
    titulo       text NOT NULL,
    inicio       timestamptz NOT NULL,
    fin          timestamptz NOT NULL,
    descripcion  text,
    etiquetas    text[] NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------
-- Bitácora de la ingesta: qué corrió, cuándo, si falló
-- ---------------------------------------------------------------------
CREATE TABLE ingesta_log (
    id        bigserial PRIMARY KEY,
    tarea     text NOT NULL,
    inicio    timestamptz NOT NULL,
    fin       timestamptz,
    ok        boolean,
    filas     int,
    mensaje   text
);
CREATE INDEX ingesta_log_tarea ON ingesta_log (tarea, inicio DESC);

-- =====================================================================
-- Vistas para el análisis
-- =====================================================================

-- "Día pluviométrico": de 9 a 9 hora Argentina (12 a 12 UTC). La lluvia
-- caída entre las 9 del día D-1 y las 9 del día D se asigna al día D
-- (convención del SMN: se lee el pluviómetro a las 9 y se anota ese día).
CREATE FUNCTION dia_pluviometrico(t timestamptz) RETURNS date
LANGUAGE sql IMMUTABLE AS $$
    SELECT ((t AT TIME ZONE 'UTC') + interval '12 hours' - interval '1 microsecond')::date
$$;

-- Lluvia diaria de todas las estaciones (solo datos que no son "malos").
CREATE VIEW lluvia_diaria AS
SELECT dia_pluviometrico(o.ts) AS dia,
       o.estacion_id,
       e.nombre,
       e.tipo,
       round(sum(o.valor)::numeric, 1) AS lluvia_mm,
       count(*) AS registros
FROM observacion o
JOIN estacion e ON e.id = o.estacion_id
WHERE o.variable = 'precip' AND o.qc <> 4
GROUP BY 1, 2, 3, 4;

-- Lo mismo + los reportes de lluvia de la comunidad, con el tipo de
-- instrumento como metadato, para comparar todo en una sola tabla.
CREATE VIEW lluvia_diaria_todas AS
SELECT dia, estacion_id AS origen, nombre, tipo AS tipo_origen,
       CASE tipo WHEN 'ema' THEN 'estacion_automatica'
                 WHEN 'convencional' THEN 'pluviometro_convencional'
                 WHEN 'pluviometro' THEN 'pluviometro_convencional'
                 ELSE tipo END AS instrumento,
       lluvia_mm
FROM lluvia_diaria
UNION ALL
SELECT dia_pluviometrico(ts), 'comunidad:' || coalesce(escuela, '?'), coalesce(reportero, ''),
       'comunidad', instrumento, lluvia_mm::numeric
FROM reporte_comunidad
WHERE tipo = 'lluvia' AND lluvia_mm IS NOT NULL AND qc <> 4;

-- Promedios horarios (las variables de lluvia se suman, el resto se promedia).
CREATE VIEW obs_horaria AS
SELECT date_trunc('hour', ts) AS hora, estacion_id, variable,
       CASE WHEN variable = 'precip' THEN sum(valor) ELSE avg(valor) END AS valor,
       min(valor) AS minimo, max(valor) AS maximo, count(*) AS n
FROM observacion
WHERE qc <> 4
GROUP BY 1, 2, 3;

-- Último dato de cada estación y variable (para el "estado de la red").
CREATE VIEW ultimo_dato AS
SELECT DISTINCT ON (estacion_id, variable)
       estacion_id, variable, ts, valor, qc
FROM observacion
ORDER BY estacion_id, variable, ts DESC;
