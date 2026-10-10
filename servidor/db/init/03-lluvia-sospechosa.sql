-- =====================================================================
-- 03: la lluvia diaria informa cuánta lluvia SOSPECHOSA (qc = 3) incluye.
--
-- La lluvia sospechosa se sigue sumando (no se descarta sin pruebas), pero la
-- columna mm_sospechosos permite verla y excluirla en un análisis.
--
-- Base ya existente: aplicar una vez (es seguro repetirlo):
--   docker compose exec -T db psql -U meteo -d meteo < db/init/03-lluvia-sospechosa.sql
-- =====================================================================

-- Las vistas no guardan datos (son consultas con nombre): se borran y se
-- vuelven a crear, así el archivo se puede aplicar las veces que haga falta.
BEGIN;
DROP VIEW IF EXISTS lluvia_diaria_todas;
DROP VIEW IF EXISTS lluvia_diaria;

CREATE VIEW lluvia_diaria AS
SELECT dia_pluviometrico(o.ts) AS dia,
       o.estacion_id,
       e.nombre,
       e.tipo,
       round(sum(o.valor)::numeric, 1) AS lluvia_mm,
       count(*) AS registros,
       round(coalesce(sum(o.valor) FILTER (WHERE o.qc = 3), 0)::numeric, 1) AS mm_sospechosos
FROM observacion o
JOIN estacion e ON e.id = o.estacion_id
WHERE o.variable = 'precip' AND o.qc <> 4
GROUP BY 1, 2, 3, 4
UNION ALL
SELECT dia_pluviometrico(o.ts) AS dia,
       o.estacion_id,
       e.nombre,
       e.tipo,
       round(o.valor::numeric, 1) AS lluvia_mm,
       1::bigint AS registros,
       CASE WHEN o.qc = 3 THEN round(o.valor::numeric, 1) ELSE 0 END AS mm_sospechosos
FROM observacion o
JOIN estacion e ON e.id = o.estacion_id
WHERE o.variable = 'precip_24h' AND o.qc <> 4
  AND extract(hour FROM o.ts AT TIME ZONE 'UTC') = 12
  AND extract(minute FROM o.ts AT TIME ZONE 'UTC') = 0;

CREATE VIEW lluvia_diaria_todas AS
SELECT dia, estacion_id AS origen, nombre, tipo AS tipo_origen,
       CASE tipo WHEN 'ema' THEN 'estacion_automatica'
                 WHEN 'convencional' THEN 'pluviometro_convencional'
                 WHEN 'pluviometro' THEN 'pluviometro_convencional'
                 WHEN 'sinoptica' THEN 'pluviometro_convencional'
                 ELSE tipo END AS instrumento,
       lluvia_mm,
       mm_sospechosos
FROM lluvia_diaria
UNION ALL
SELECT dia_pluviometrico(ts), 'comunidad:' || coalesce(escuela, '?'), coalesce(reportero, ''),
       'comunidad', instrumento, lluvia_mm::numeric,
       CASE WHEN qc = 3 THEN lluvia_mm::numeric ELSE 0 END
FROM reporte_comunidad
WHERE tipo = 'lluvia' AND lluvia_mm IS NOT NULL AND qc <> 4;
COMMIT;
