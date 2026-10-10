# 4. Visualizador web

Se abre en el navegador:

- desde tu casa: **http://192.168.0.100:8080**
- desde cualquier lugar con Tailscale: **http://100.84.41.62:8080**

(El puerto se puede cambiar con `WEB_PUERTO=` en `.env`.) No está publicado a
internet: el router no lo expone, así que solo lo ven los equipos de tu red
y los de tu cuenta de Tailscale.

## Pestañas

Las EMAs y las estaciones de superficie del SMN son datos de naturaleza
distinta, por eso cada red tiene su vista:

| | EMAs (automáticas) | Estaciones SMN (sinópticas) |
|---|---|---|
| Frecuencia | cada 5–15 min | cada 1–3 h |
| Qué miden | pocas variables, continuas | muchas: también nubes, visibilidad, tiempo presente |
| Vista | **meteograma** (paneles apilados) | **tabla de observaciones** (como el parte del SMN) |
| Lluvia | por hora (barras) | por períodos fijos (6 h, 24 h) |

| Pestaña | Qué muestra |
|---|---|
| **Mapa** | ● EMAs y ■ estaciones SMN, con filtros para ver cada red. Color = estado: ✓ al día, ! demorada, ✕ sin datos. Tocá una estación para ir a su vista. |
| **EMAs** | Resumen de hoy de toda la red (temperatura, máx/mín desde las 00, humedad, lluvia desde las 9) y el **meteograma** de la estación elegida: temperatura y rocío, humedad, presión, lluvia por hora, viento, dirección y radiación (solo los paneles de lo que la estación mide). Todos los paneles comparten el eje de tiempo y quedan alineados. Si la estación mide viento, abajo aparece la **rosa de los vientos** del período. |
| **Estaciones SMN** | Resumen de los últimos días (máx/mín de las observaciones horarias y lluvia de 24 h de las 9) y la **tabla de observaciones** de la estación elegida: tiempo presente en palabras (código ww de la OMM), nubosidad en octavos, temperatura, rocío, humedad (calculada de T y Td), viento con **barbas** (convención del hemisferio sur: el palo apunta de dónde viene el viento; media pluma = 5 nudos, pluma = 10, triángulo = 50; círculo = calma), presión, visibilidad, lluvia por período y extremos. Pasando el mouse por la hora se ve el mensaje SYNOP original. Rosa de los vientos de las observaciones del período. Descarga en CSV. |
| **Comparar** | Una variable, hasta 8 estaciones de cualquier red, de 24 h a 1 año. Cortes de datos visibles, lluvia acumulada, tabla y CSV. |
| **Lluvia diaria** | Tabla de 9 a 9 de todas las fuentes, incluidos los reportes de la comunidad con su instrumento. "–" = sin datos (no es lo mismo que 0); ⚠ = incluye lluvia sospechosa. |
| **Estado de la red** | Última corrida de cada tarea de ingesta y sus errores. |

## Cómo está hecho

```
navegador ──► web (FastAPI, puerto 8080)
                ├── /              la página: app/meteo/web/static/index.html
                ├── /api/...       los datos, en JSON o CSV
                └── /api/docs      documentación automática de la API (probala)
                         │
                         ▼  SOLO LECTURA (la API no puede modificar la base)
                    base de datos
```

- **API** (`app/meteo/web/app.py`): cada dirección `/api/...` hace una consulta SQL
  y devuelve el resultado. Se puede usar desde cualquier programa:

  ```python
  import pandas as pd
  url = "http://100.84.41.62:8080/api/series?variable=temp&estacion=bavio&estacion=omm_87593&dias=30&formato=csv"
  df = pd.read_csv(url, parse_dates=["ts"])
  ```

  Otras direcciones útiles: `/api/estacion/{id}/meteograma?dias=2`,
  `/api/emas/resumen`, `/api/sinoptica/{id}/observaciones?dias=1&formato=csv`,
  `/api/sinopticas/resumen?dias=3`. Todas están documentadas en `/api/docs`.

  Parámetros de `/api/series`: `variable`, `estacion` (repetible, máx. 8),
  `dias` o `desde`/`hasta` (ISO, UTC), `paso` (`crudo`, `hora`, `dia` o `auto`),
  `formato` (`json` o `csv`). Con `auto`: crudo hasta 3 días, horario hasta 60 y
  diario después. La lluvia se suma; lo demás se promedia; los datos con qc = 4 no
  se incluyen.

- **Página**: HTML + JavaScript sin "frameworks". Usa Leaflet (mapa) y Chart.js
  (gráficos), guardados dentro del servidor (`static/vendor/`), así que no
  depende de servicios externos (salvo los mosaicos del mapa de OpenStreetMap).
- **Colores**: paleta probada para daltonismo; cada estación conserva su color
  mientras esté seleccionada; los estados llevan ícono y texto, no solo color.
  Tiene modo oscuro automático (según la configuración del dispositivo).

## Rosa de los vientos

16 direcciones; el largo de cada pétalo es el % del tiempo (o de las
observaciones) en que el viento vino de esa dirección, apilado por velocidad
(2–10, 10–20, 20–30 y 30+ km/h, de claro a oscuro). Las calmas (< 2 km/h) se
informan aparte.

## Promedios de la dirección del viento

Cuando se agrega por hora o por día, la dirección del viento se promedia en
forma **circular** (promedio de seno y coseno): el promedio común de 350° y 10°
daría 180° (sur) cuando los dos son vientos casi del norte.
