# 2. Los datos: de dónde vienen, cómo se guardan, cómo se consultan

## 2.1 Fuentes

| Fuente | Qué trae | Cada | Recolector | Historia disponible |
|---|---|---|---|---|
| ThingSpeak | EMAs Bavío, San Vicente, EMA Blanca, Campbell, EMA Verde | 15 min | `fuentes/thingspeak.py` | La que guarde cada canal |
| Wunderground | ES7/EP20, Los Talas, Monte Veloz (y Daza, de baja) | 15 min | `fuentes/wunderground.py` | Varios años, día por día |
| Davis FCAG | Observatorio (archivo `downld08.txt`) | 15 min | `fuentes/davis.py` | Solo los últimos días: **por eso importa archivarla** |
| Ogimet | SYNOP de 10 estaciones del SMN | 1 h | `fuentes/ogimet.py` + `synop.py` | Años |
| Google Sheet | Reportes de la comunidad (pestaña "Reportes") | 30 min | `fuentes/comunidad.py` | Todo |
| Apps Script Pronóstico UNLP | Emisión vigente de cada ciudad | 1 h | `fuentes/pronosticos.py` | Solo desde que corre el servidor |

Todo se configura en `config/estaciones.yaml`. **Las Google Sheets siguen
siendo la fuente original**: el servidor solo lee y copia, no les escribe.

### Rellenar el pasado

```bash
docker compose exec ingesta python -m meteo.ingesta historico ogimet --desde 2025-01-01 --hasta 2026-10-01
docker compose exec ingesta python -m meteo.ingesta historico wunderground --desde 2025-06-01 --hasta 2026-10-01
docker compose exec ingesta python -m meteo.ingesta historico thingspeak --desde 2026-01-01 --hasta 2026-10-01 --estaciones bavio
```

Se puede correr las veces que haga falta: nunca duplica.

> Ogimet bloquea a quien pide mucho seguido. El recolector espera 8 s entre
> pedidos de 7 días; un año de una estación son ~52 pedidos (~7 min).

## 2.2 Modelo de datos

```
variable ──┐                       estacion ─── instrumento (historial de sensores)
           │                          │
           └──── observacion ─────────┘        synop_crudo (mensajes originales)
                 (ts, estacion, variable, valor, qc)

reporte_comunidad (con instrumento + período)     pronostico_emision ── pronostico_dia
evento (eventos significativos)                    ingesta_log (bitácora)
```

**Formato largo.** En vez de una tabla con columnas `temp, hum, presion...`,
hay una fila por cada valor:

| ts | estacion_id | variable | valor | qc |
|---|---|---|---|---|
| 2026-10-10 12:00Z | bavio | temp | 14.6 | 1 |
| 2026-10-10 12:00Z | bavio | hum | 70 | 1 |

Ventaja: una estación con radiación solar y otra sin ella conviven sin
columnas vacías, y agregar una variable es una fila en la tabla `variable`.

**Unidades.** Todo se guarda en una sola unidad por variable (ver tabla
`variable`): °C, hPa, mm, **m/s** (Wunderground y Davis dan km/h; SYNOP a
veces nudos: se convierten al entrar).

**Tiempo.** Todo en UTC. Para ver hora argentina:
`ts AT TIME ZONE 'America/Argentina/Buenos_Aires'`.

**Lluvia.** La variable `precip` es siempre "lo que llovió en el intervalo
que termina en `ts`". Cada fuente lo trae distinto y el recolector lo
uniformiza: ThingSpeak ya viene por intervalo; Wunderground trae el
acumulado del día y se calculan las diferencias; la Davis viene por
intervalo. Las sinópticas traen períodos fijos (`precip_6h`, `precip_24h`...),
que se guardan aparte porque se superponen.

**Día pluviométrico.** La función `dia_pluviometrico(ts)` asigna la lluvia
de 9 a 9 (hora argentina) al día de la lectura, como el SMN.

### Control de calidad (`qc`)

| qc | Significado |
|---|---|
| 0 | Sin controlar |
| 1 | Pasó el control de rango físico |
| 3 | Sospechoso. Hoy: lluvia de Wunderground registrada justo antes de que la estación "borre" su acumulado fuera de la medianoche (patrón 0 → 0,25 → 0, visto en Los Talas). Se suma igual, pero la tabla de lluvia lo marca con ⚠ y la columna `mm_sospechosos` permite excluirlo. Más adelante: saltos, persistencia, comparación con vecinas. |
| 4 | Malo: fuera de los límites físicos de la tabla `variable` |

Nada se borra. Las vistas de análisis excluyen `qc = 4`; los `qc = 3` se
incluyen pero quedan identificados.

### Cambios en la estructura de la base (migraciones)

Los archivos de `db/init/` se ejecutan **solos solo la primera vez** que se
crea la base. Cuando se agrega uno nuevo, en una base que ya existe hay que
aplicarlo a mano, **en orden**:

```bash
docker compose exec -T db psql -U meteo -d meteo < db/init/02-lluvia-sinopticas.sql
docker compose exec -T db psql -U meteo -d meteo < db/init/03-lluvia-sospechosa.sql
```

Son seguros de repetir (solo recrean vistas, que no guardan datos).

### Metadatos

- `estacion.tipo`: `ema`, `convencional`, `sinoptica`, `pluviometro`, `otra`.
- `instrumento`: qué sensor midió cada variable y **desde/hasta cuándo**. Si
  se cambia un pluviómetro, se cierra el registro (`hasta`) y se abre otro.
  Sin esto no se puede saber si un salto en una serie es climático o del
  instrumento.
- `reporte_comunidad.instrumento`: `pluviometro_convencional`,
  `pluviometro_casero`, `estacion_automatica`, `estimacion` o `sin_dato`.
- `reporte_comunidad.periodo`: `24h_9hs`, `desde_ultima_lectura`, `evento`,
  `sin_dato`.

Los dos últimos salen de los campos nuevos del formulario de
`ciencia-ciudadana.html`. **Para que funcionen:**

1. Pegar el `scripts/apps-script-reportes.gs` actualizado en el Apps Script
   de la planilla y crear una **nueva versión** del despliegue.
2. Agregar los encabezados `Instrumento` y `Periodo` al final de la pestaña
   **Reportes** (la de "Pendientes" se completa sola), y copiarlos junto con
   el resto al pasar reportes de Pendientes a Reportes.

## 2.3 Consultas útiles

Entrar a la base: `docker compose exec db psql -U meteo -d meteo`.

```sql
-- ¿Qué estaciones están transmitiendo? (último dato de temperatura)
SELECT estacion_id, ts AT TIME ZONE 'America/Argentina/Buenos_Aires' AS hora_local, valor
FROM ultimo_dato WHERE variable = 'temp' ORDER BY ts DESC;

-- Lluvia diaria de octubre, todas las fuentes, con el tipo de instrumento
SELECT * FROM lluvia_diaria_todas
WHERE dia BETWEEN '2026-10-01' AND '2026-10-31' ORDER BY dia, origen;

-- Máxima y mínima diaria (día civil, hora argentina) de cada EMA
SELECT (ts AT TIME ZONE 'America/Argentina/Buenos_Aires')::date AS dia, estacion_id,
       max(valor) AS tmax, min(valor) AS tmin, count(*) AS n
FROM observacion WHERE variable = 'temp' AND qc <> 4
GROUP BY 1, 2 ORDER BY 1, 2;

-- Verificación de pronósticos: error de la máxima según el plazo
SELECT pd.plazo_dias, count(*) AS casos,
       round(avg(pd.temp_max - obs.tmax)::numeric, 1) AS sesgo,
       round(avg(abs(pd.temp_max - obs.tmax))::numeric, 1) AS error_medio_abs
FROM pronostico_dia pd
JOIN pronostico_emision pe ON pe.id = pd.emision_id AND pe.ciudad = 'La Plata'
JOIN (SELECT (ts AT TIME ZONE 'America/Argentina/Buenos_Aires')::date AS dia, max(valor) AS tmax
      FROM observacion WHERE estacion_id = 'omm_87593' AND variable = 'temp' AND qc <> 4
      GROUP BY 1) obs ON obs.dia = pd.fecha
GROUP BY 1 ORDER BY 1;

-- ¿Qué falló en la ingesta en las últimas 24 h?
SELECT tarea, inicio, mensaje FROM ingesta_log
WHERE NOT ok AND inicio > now() - interval '24 hours' ORDER BY inicio DESC;

-- Cargar un evento significativo
INSERT INTO evento (titulo, inicio, fin, descripcion, etiquetas)
VALUES ('Tormenta con granizo en La Plata', '2026-10-14 18:00-03', '2026-10-15 02:00-03',
        'Línea de inestabilidad...', '{granizo,convección}');
```

### Desde Python (para análisis)

La base escucha solo en la propia PC (`127.0.0.1:5432`). Desde otra compu,
abrí un túnel SSH: `ssh -L 5432:localhost:5432 usuario@ip-del-servidor`, y
después:

```python
import pandas as pd
from sqlalchemy import create_engine

motor = create_engine("postgresql+psycopg://meteo:LA_CONTRASEÑA@localhost:5432/meteo")
df = pd.read_sql("""
    SELECT ts, estacion_id, valor FROM observacion
    WHERE variable = 'temp' AND qc <> 4 AND ts > now() - interval '7 days'
""", motor)
tabla = df.pivot(index="ts", columns="estacion_id", values="valor")
tabla.resample("1h").mean().plot()
```

## 2.4 Agregar una estación nueva

1. Copiar un bloque en `config/estaciones.yaml` y completar `id` (único, sin
   espacios), `nombre`, `tipo`, `fuente`, coordenadas y `config`.
2. `docker compose restart ingesta`.
3. Opcional: rellenar su pasado con `historico`.

Para un pluviómetro de escuela que se carga a mano, usar `fuente: manual` (no
se consulta a ningún lado) y cargar las lecturas por SQL o, más adelante,
desde un formulario.

## 2.5 Cosas a verificar con quien administra cada estación

Están marcadas con `VERIFICAR` en `config/estaciones.yaml`:

- **ThingSpeak, `field6`:** se asumió presión a nivel de estación.
- **Davis:** unidad del viento (se asumió km/h) y si "Bar" está reducida al
  nivel del mar (se asumió que sí).
- **Sinópticas:** coordenadas aproximadas; conviene tomarlas del listado
  oficial del SMN/OMM.
