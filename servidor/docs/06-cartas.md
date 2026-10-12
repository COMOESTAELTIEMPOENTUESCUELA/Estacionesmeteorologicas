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
| Observaciones de superficie | Superficie | Ogimet (pendiente) |

Solo se guardan **análisis** (diagnóstico), no pronósticos.

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

## Comandos

```bash
# Bajar ahora (sin esperar a la vuelta de cada hora):
docker compose exec ingesta python -m meteo.ingesta una cartas
# Ver qué hay archivado:
ls -R /srv/meteo/archivo/cartas | head
```

## Espacio

Unas 4 cartas × 4 horas × ~200 KB ≈ 3 MB por día, 1 GB por año.

## Para sumar otra carta

Agregar un bloque en `config/cartas.yaml` con `id`, `nombre`, `nivel`,
`fuente: bentley`, `dominio` y `variable` (los de la dirección de la página),
y reiniciar: `docker compose up -d ingesta web`.

## Pendiente

- **Ogimet** (mapa de observaciones de superficie): falta confirmar en qué
  dirección está la imagen dentro de la página. Ogimet genera los mapas para
  cualquier fecha, así que ahí sí se puede rellenar el historial desde 2018.
- **Satélite**: sumar a la grilla las imágenes del módulo GOES-19.
- **Cartas propias desde el GFS / ERA5**, para tener todo el historial desde 2018.
