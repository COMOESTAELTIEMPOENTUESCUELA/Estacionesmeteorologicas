# 4. Visualizador web

Se abre en el navegador:

- desde tu casa: **http://192.168.0.100:8080**
- desde cualquier lugar con Tailscale: **http://100.84.41.62:8080**

(El puerto se puede cambiar con `WEB_PUERTO=` en `.env`.) No está publicado a
internet: el router no lo expone, así que solo lo ven los equipos de tu red
y los de tu cuenta de Tailscale.

## Pestañas

| Pestaña | Qué muestra |
|---|---|
| **Mapa** | Cada estación con su último dato y su estado: ✓ al día, ! demorada, ✕ sin datos. Las sinópticas tienen más margen porque reportan cada 1–3 h. Tocá una estación para ver sus series. |
| **Series** | Una variable, hasta 8 estaciones, desde 24 h hasta 1 año. Los cortes de datos se ven como cortes (la línea no inventa valores). La lluvia se muestra acumulada en el período. Botón para descargar CSV y tabla con los números. |
| **Lluvia diaria** | Tabla de 9 a 9 de todas las fuentes, incluidos los reportes de la comunidad con su instrumento. "–" = sin datos (no es lo mismo que 0). |
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
