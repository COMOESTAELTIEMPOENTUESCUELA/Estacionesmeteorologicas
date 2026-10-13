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

### Satélite (NOAA STAR)

Infrarrojo (canal 13) y GeoColor del GOES-19 para el sector "ssa" (sur de
Sudamérica), 1800x1080, ya coloreadas por NOAA (gratis). Solo a las horas
de las cartas: ~8 imágenes por día, unos 6–8 MB de internet y ~2,5 GB por
año de disco. NOAA guarda cada imagen con su fecha en el nombre
(`AAAADDDHHMM_GOES19-ABI-ssa-13-1800x1080.jpg`, con DDD = día del año) unos
pocos días: la tarea lee la lista de la carpeta y elige la más cercana a
cada hora sinóptica (±10 min), mirando 48 horas hacia atrás.

NOAA guarda unos 10 días (probado el 14/10/2026: había desde el 3/10). Para
rellenar un período dentro de eso (ej. para analizar un evento):

```bash
docker compose exec ingesta python -m meteo.ingesta historico cartas --desde 2026-10-06 --hasta 2026-10-14
```

Para probar que el servidor llega a NOAA:

```bash
curl -s https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/13/ | grep -o '[0-9]\{11\}_GOES19-ABI-ssa-13-1800x1080.jpg' | tail -3
```

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

**Los mapas son transparentes**: solo traen los valores de las estaciones.
La geografía (costas y límites, `base=bluem`) la pone la página por debajo,
como otra imagen. Esa imagen no cambia: el servidor busca en la página la
que nombra la zona o la base, la baja **una vez** a
`cartas/_fondos/ogimet_amersurs.png` y el visualizador la pone de fondo de
cada mapa (por eso al principio se veían los recuadros sobre negro). Si
alguna vez queda mal, se borra ese archivo y se vuelve a bajar en la
próxima vuelta. Para ver qué arma la página:

```bash
docker compose exec ingesta python -m meteo.ingesta diagnostico-ogimet
```

## En el visualizador

- **Placa** (de entrada): todas las cartas de esa hora en una pantalla, como
  la placa del equipo. A las 00, 06 y 18 UTC no ocupan lugar las que solo
  se informan a las 12 UTC. **Grandes**: de a dos por fila.
- Tocando una carta se abre en grande con **zoom**: rueda del mouse o
  pellizco, arrastrar para moverse, doble clic acerca o vuelve. **← →**
  cambian la hora *sin perder el zoom* (sirve para seguir un sistema sobre
  la misma zona) y **↑ ↓** pasan a la carta siguiente.

## Herramientas de análisis

- **Dibujar** (visor grande, barra "Dibujar"): frentes frío, cálido,
  ocluido y estacionario con sus símbolos, centros A y B, texto, flechas y
  lápiz. Los símbolos de un frente van a la izquierda del sentido en que se
  dibujó; ⇄ los da vuelta. Se guarda solo en el servidor (tabla
  `carta_dibujo`, una por carta y hora; la web la crea al arrancar) y se ve
  superpuesto en la placa (✏️). Deshacer con Ctrl+Z. "⬇ PNG" descarga la
  carta con el dibujo y el título. Es vectorial (SVG en un lienzo de 1000 de
  ancho): se ve nítido con cualquier zoom. Es la única escritura de la web,
  y va por una conexión aparte.
  En la **copia pública** también se puede dibujar, pero cada uno lo suyo:
  se guarda en SU navegador (localStorage), no en el servidor, que ahí no
  acepta escrituras. Los dibujos oficiales se ven debajo, sin poder
  tocarlos, y el PNG descargado lleva los dos.
- **Animar** (visor grande): recorre la carta 1 a 7 días hacia atrás,
  precargando las imágenes; barra espaciadora para pausar, barra para ir a
  un cuadro. Al pausar, la página queda en esa hora.
- **Cursor sincronizado** (placa): una cruz marca el mismo punto en las
  cartas de la misma proyección (las de Bentley entre sí; las de Ogimet
  entre sí).
- **Superponer** (visor grande): otra carta del mismo grupo encima, con
  transparencia.
- **Observaciones de la hora**: debajo, el SYNOP de esa hora de cada
  estación del SMN.

## Pronóstico del grupo para el día de la carta

Debajo de las cartas está lo que el grupo pronosticó para ese día en las 6
ciudades (mapa y tabla), contra lo observado, con el mismo cálculo que la
pestaña Verificación (`/api/verificacion/dia?fecha=AAAA-MM-DD&plazo=0`).

- El día es el **civil argentino**: la carta de las 00 UTC del 13 es el 12 a
  las 21 hs, así que muestra el pronóstico del 12.
- "Pronóstico **del mismo día**" (plazo 0) es el que viene de entrada; se
  puede elegir el del día anterior, o de 2 o 3 días antes, para ver cómo se
  fue ajustando el pronóstico a medida que se acercaba el evento.
- Tocando una ciudad se ve el pronóstico por período (mañana, tarde, noche),
  la suburbana de La Plata, quién lo hizo y cuándo se publicó.

## Compartir solo las cartas (copia pública)

El contenedor `web-cartas` es una copia del visualizador en "modo cartas"
(`WEB_MODO=cartas`): muestra solo esta pestaña y el panel del pronóstico
del grupo (con quién pronosticó: el grupo decidió que figure). El SERVIDOR rechaza cualquier
otra dirección (EMAs, SMN, verificación completa, descargas): no depende
de que la página las oculte.

Escucha solo en la propia máquina (`127.0.0.1:8081`), así que se comparte
únicamente a través de Tailscale Funnel:

```bash
sudo tailscale funnel --bg 8081          # abrir: https://<nombre>.ts.net/
tailscale funnel status                  # ver qué está abierto
sudo tailscale funnel --https=443 off    # cerrar
```

El visualizador completo (puerto 8000) sigue privado. Si se quiere que la
copia pública pida contraseña, completar `CARTAS_USUARIO` y `CARTAS_CLAVE`
en `.env` (distintas de las del visualizador completo) y
`docker compose up -d web-cartas`.

## Comandos

```bash
# Bajar ahora (sin esperar a la vuelta de cada hora):
docker compose exec ingesta python -m meteo.ingesta una cartas
# Ver qué hay archivado:
ls -R /srv/meteo/archivo/cartas | head
```

## Velocidad: miniaturas y caché del navegador

Las cartas salen por la SUBIDA de internet de la casa, que es lenta. Una
hora completa pesa ~1 MB, casi todo de las 4 de Bentley (~200 KB c/u).

- **Miniaturas**: al archivar una carta de más de 100 KB se guarda además
  una versión de 640 px en WebP (`cartas/_min/...`), 5 a 10 veces más
  liviana. La vista "placa" usa la miniatura; la completa se baja recién al
  abrirla en grande. La tarea de cartas completa sola las que falten (ej.
  las archivadas antes de que existiera esto).
- **Caché**: una carta archivada no cambia nunca, así que el servidor le
  dice al navegador que la guarde (`Cache-Control: immutable`): volver a
  una hora ya vista es instantáneo.

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
