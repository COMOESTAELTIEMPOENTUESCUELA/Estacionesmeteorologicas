-- =====================================================================
-- 02: las sinópticas entran en la lluvia diaria.
--
-- El SYNOP no informa lluvia "por intervalo" sino por períodos fijos que se
-- superponen (6 h, 12 h, 24 h). Sumarlos contaría la misma lluvia varias
-- veces. Pero el SMN manda a las 12 UTC (9 hora argentina) el acumulado de
-- 24 h: es exactamente el día pluviométrico de 9 a 9. Ese es el valor que se
-- usa para la lluvia diaria de las sinópticas.
--
-- En una instalación nueva se aplica solo (va en db/init). En una base que ya
-- existe, se aplica una vez a mano:
--   docker compose exec -T db psql -U meteo -d meteo < db/init/02-lluvia-sinopticas.sql
-- (Es seguro correrlo más de una vez.)
-- =====================================================================

CREATE OR REPLACE VIEW lluvia_diaria AS
-- EMAs, convencionales, pluviómetros: suma de la lluvia por intervalo.
SELECT dia_pluviometrico(o.ts) AS dia,
       o.estacion_id,
       e.nombre,
       e.tipo,
       round(sum(o.valor)::numeric, 1) AS lluvia_mm,
       count(*) AS registros
FROM observacion o
JOIN estacion e ON e.id = o.estacion_id
WHERE o.variable = 'precip' AND o.qc <> 4
GROUP BY 1, 2, 3, 4
UNION ALL
-- Sinópticas: el acumulado de 24 h informado a las 12 UTC.
SELECT dia_pluviometrico(o.ts) AS dia,
       o.estacion_id,
       e.nombre,
       e.tipo,
       round(o.valor::numeric, 1) AS lluvia_mm,
       1::bigint AS registros
FROM observacion o
JOIN estacion e ON e.id = o.estacion_id
WHERE o.variable = 'precip_24h' AND o.qc <> 4
  AND extract(hour FROM o.ts AT TIME ZONE 'UTC') = 12
  AND extract(minute FROM o.ts AT TIME ZONE 'UTC') = 0;

-- Igual que antes, pero ahora las sinópticas figuran como pluviómetro
-- convencional (es lo que usa el SMN), para compararlas con todo lo demás.
CREATE OR REPLACE VIEW lluvia_diaria_todas AS
SELECT dia, estacion_id AS origen, nombre, tipo AS tipo_origen,
       CASE tipo WHEN 'ema' THEN 'estacion_automatica'
                 WHEN 'convencional' THEN 'pluviometro_convencional'
                 WHEN 'pluviometro' THEN 'pluviometro_convencional'
                 WHEN 'sinoptica' THEN 'pluviometro_convencional'
                 ELSE tipo END AS instrumento,
       lluvia_mm
FROM lluvia_diaria
UNION ALL
SELECT dia_pluviometrico(ts), 'comunidad:' || coalesce(escuela, '?'), coalesce(reportero, ''),
       'comunidad', instrumento, lluvia_mm::numeric
FROM reporte_comunidad
WHERE tipo = 'lluvia' AND lluvia_mm IS NOT NULL AND qc <> 4;
