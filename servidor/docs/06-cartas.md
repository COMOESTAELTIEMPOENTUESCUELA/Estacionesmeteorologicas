# 6. Cartas de diagnóstico (pestaña "Cartas")

Para el análisis de eventos significativos: en vez de bajar carta por carta
y pegarlas en una presentación, el servidor las **archiva solo** y la pestaña
**Cartas** las muestra con un selector de fecha y hora (00, 06, 12 y 18 UTC).

## Qué se guarda

Configurado en `config/cartas.yaml`:

| Producto | Nivel | Fuente |
|---|---|---|
| Presión, espesor 1000/500 hPa y corriente en chorro (`mslp_jet`) | Superficie y 1000/500 hPa | Alicia Bentley, GFS |
| Temperatura potencial equivalente (`850_thetae`) | 850 hPa | Alicia Bentley, GFS |
| Transporte integrado de vapor y convergencia (`IVT_conv`) | 700 hPa | Alicia Bentley, GFS |
| Vorticidad relativa (`rel_vort`) | 500 hPa | Alicia Bentley, GFS |
| Tiempo presente (observaciones, código `Sg`) | Superficie | Ogimet |
| Temperatura (observaciones, código `Te`) | Superficie | Ogimet |
| Precipitación acumulada (observaciones, código `R4`), solo 12 UTC | Superficie | Ogimet |
| Temperatura máxima y mínima (observaciones, `Tx` y `Tn`), solo 12 UTC | Superficie | Ogimet |

Solo se guardan **análisis** (diagnóstico), no pronósticos.

Cada carta puede tener sus propias horas (`horas_utc` en el YAML): la
precipitación de 24 h y las temperaturas extremas se informan a las 12 UTC,
así que esos mapas solo se guardan a esa hora. En la pestaña, a otra hora,
muestran un aviso con un botón para ir a las 12 UTC del mismo día.

## Cómo funciona

La tarea `cartas` del servicio de ingesta corre cada hora:

1. Lee la página de Bentley (`standard.php`): ahí están la corrida que muestra
   (`String(2026101206`) y el número del cuadro inicial (`startFrame = 57`).
2. Calcula la hora de cada cuadro igual que la página (`functions_gfs.js`):
   **hora válida = corrida + (cuadro − startFrame) × 6 h**. Los cuadros 1 a 57
   son los análisis de los últimos 14 días; del 58 en adelante, pronósticos
   (no se bajan).
3. Baja los que todavía no están archivados (una imagen por segundo, para no
   cargar su servidor) y los guarda en
   `ARCHIVO_DIR/cartas/<producto>/<año>/<mes>/<producto>_<AAAAMMDDHH>.png`.

Como la página guarda 14 días, **el archivo arranca con las dos semanas
anteriores** a la primera descarga, y de ahí en adelante se completa solo.
Si el servidor estuvo apagado menos de 14 días, al volver recupera lo que
faltó.

**Cuidado que se tuvo:** cuando sale una corrida nueva, todos los cuadros se
renumeran. Si se bajara justo mientras la página se actualiza, una imagen de
la corrida anterior quedaría con la fecha corrida 6 horas. Por eso cada
imagen tiene que haber sido modificada (cabecera `Last-Modified`) después
del inicio de la corrida; si no, se espera a la vuelta siguiente.

Lo descargado se controla: si no es una imagen de verdad (por ejemplo, una
página de error), no se guarda.

### Ogimet

Una consulta a `gsynop` genera unos 12 mapas de esa hora como imágenes
**temporales** (`/tmp/202610121200_amersurs_Sg.png`): se bajan enseguida.
El nombre trae la fecha y la hora, y se controla que sea la pedida antes de
guardar. Códigos: Te temperatura, Td rocío, Tx máxima, Tn mínima, y Sg, R2,
R4, Pr, Hr, Wi, Dt, Hi (el texto de cada uno está en el ALT de la imagen;
se eligen en `config/cartas.yaml` con `codigo`).

En la vuelta de cada hora se completan las últimas 24 horas, respetando su
límite de una consulta cada 20 segundos.

**Ogimet solo genera estos mapas para el último día** (probado el 12/10/2026:
para hoy devuelve los 12 mapas; para hace 2 días o una semana, una
respuesta vacía). No se puede rellenar el historial: el archivo empieza el
día que se activó y se completa a diario. Una respuesta vacía no se
reintenta (no es un rechazo por el límite: esa hora ya no está).

## Comandos

```bash
# Bajar ahora (sin esperar a la vuelta de cada hora):
docker compose exec ingesta python -m meteo.ingesta una cartas
# Ver qué hay archivado:
ls -R /srv/meteo/archivo/cartas | head
```

## Espacio

Unas 9 cartas por día y hora sinóptica × ~200 KB ≈ 6 MB por día, unos 2 GB
por año.

## Para sumar otra carta

Agregar un bloque en `config/cartas.yaml` con `id`, `nombre`, `nivel`,
`fuente: bentley`, `dominio` y `variable` (los de la dirección de la página),
y reiniciar: `docker compose up -d ingesta web`.

## Pendiente

- **Satélite**: sumar a la grilla las imágenes del módulo GOES-19.
- **Cartas propias desde el GFS / ERA5**, para tener todo el historial desde 2018.
- **Mapas de superficie propios** para fechas viejas: bajar de Ogimet los SYNOP
  de toda la región (eso sí tiene historial) y dibujar los mapas en el servidor.
