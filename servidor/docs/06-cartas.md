# 6. Cartas de diagnóstico (pestaña "Cartas")

Para el análisis de eventos significativos: en vez de bajar carta por carta
y pegarlas en una presentación, el servidor las **archiva solo** y la pestaña
**Cartas** las muestra con un selector de fecha y hora (00, 06, 12 y 18 UTC).

## Qué se guarda

Configurado en `config/cartas.yaml`:

| Producto | Nivel | Fuente |
|---|---|---|
| Presión, espesor 1000/500 hPa y chorro en 250 hPa (`gfs_presion_chorro`) | Superficie, 1000/500 y 250 hPa | **Propia**, análisis GFS |
| θe, altura y viento (`gfs_thetae_850`) | 850 hPa | **Propia**, análisis GFS |
| Transporte integrado de vapor (IVT) y presión (`gfs_ivt`) | 1000-300 hPa | **Propia**, análisis GFS |
| Vorticidad relativa, altura y viento (`gfs_vorticidad_500`) | 500 hPa | **Propia**, análisis GFS |
| Las mismas 4 de Alicia Bentley (respaldo y comparación) | | Página de Bentley, GFS |
| Tiempo presente (observaciones, código `Sg`) | Superficie | Ogimet |
| Temperatura (observaciones, código `Te`) | Superficie | Ogimet |
| Precipitación acumulada (observaciones, código `R4`), solo 12 UTC | Superficie | Ogimet |
| Temperatura máxima y mínima (observaciones, `Tx` y `Tn`), solo 12 UTC | Superficie | Ogimet |

Solo se guardan **análisis** (diagnóstico), no pronósticos.

Cada carta puede tener sus propias horas (`horas_utc` en el YAML): la
precipitación de 24 h y las temperaturas extremas se informan a las 12 UTC,
así que esos mapas solo se guardan a esa hora. En la pestaña, a otra hora,
muestran un aviso con un botón para ir a las 12 UTC del mismo día.

## Cartas propias con el análisis del GFS (en pausa)

> **Apagadas desde el 29/10/2026:** las cuatro tienen `activo: false` en
> `config/cartas.yaml`: no se dibujan, no se baja nada del GFS y no se
> muestran. Las cartas de diagnóstico son las de Bentley (análisis y
> pronóstico). Para retomarlas, quitar esas líneas. El código sigue en el
> repositorio.

Desde el 27/10/2026 las cartas de diagnóstico también las dibuja el propio
servidor, para no depender solo de la página de Alicia Bentley (que se cayó
unos días). Las de Bentley se siguen bajando, como respaldo y para comparar. Código: `app/meteo/cartas/gfs.py` (datos) y `diagnostico.py`
(cuentas y dibujo).

**Los datos.** El análisis del GFS (hora 0 de cada corrida, 0,5°) está en el
bucket público de NOAA en Amazon, **desde 2021**:
`https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.AAAAMMDD/HH/atmos/gfs.tHHz.pgrb2.0p50.f000`.
El archivo pesa ~120 MB, pero al lado hay un índice (`.idx`) que dice en qué
byte empieza cada variable: se piden solo las ~63 que se usan (encabezado
HTTP `Range`), **~16 MB por hora sinóptica**. Se decodifican con `eccodes`
(la librería del ECMWF para GRIB2). El análisis sale ~4 h después de la hora.

**Las cuentas** (paso a paso en `diagnostico.py`):
- θe con la fórmula de Bolton (1980), a partir de temperatura y humedad relativa.
- IVT = (1/g) ∫ q·V dp entre 1000 y 300 hPa (17 niveles); los niveles bajo el
  suelo (la cordillera) no cuentan.
- Vorticidad relativa = absoluta (la da el GFS) − f. En el hemisferio sur la
  ciclónica es **negativa**: va en rojo.
- La vorticidad, la presión y el espesor se suavizan levemente (filtro de 9
  puntos), como en las cartas operativas: a 0,5° tienen "ruido" de grilla.

**El dibujo.** Proyección cónica conforme de Lambert, región configurable
(`region` en `cartas.yaml`, hoy de 95°O a 25°O y de 60°S a 5°N). Las cuatro
comparten exactamente el mismo marco: funcionan el cursor sincronizado y
"superponer" entre ellas.

**Eventos viejos.** Como NOAA guarda los análisis desde 2021, cualquier
evento se puede dibujar después:

```bash
docker compose exec ingesta python -m meteo.ingesta historico cartas --desde 2024-04-01 --hasta 2024-04-05
```

**Para sumar otra carta** (advección térmica, espesor, agua precipitable,
CAPE...): agregar las variables a `PEDIDOS`, el cálculo y el dibujo en
`diagnostico.dibujar`, y la entrada en `cartas.yaml` con `carta: <nombre>`.

## Pronóstico GFS (dibujado en el servidor, formato Bentley)

En Cartas, la tarjeta **"Pronóstico GFS"** muestra las cartas del plazo
elegido, de +0 a +192 h, con la hora válida en hora argentina y UTC.

- **Vistas:** las 4 juntas · una carta grande · 2 cartas lado a lado (mismo
  plazo) · la misma carta en 2 plazos (12, 24 o 48 h antes o después). La
  vista elegida se recuerda en el navegador.
- **Días:** botones por día ("vie 30") que llevan a las 9 hs (12 UTC) de ese día.
- **Cursor sincronizado** (el mismo casillero que el de las cartas de
  análisis): las 4 tienen el mismo marco, así que la cruz marca el mismo
  lugar en las otras cartas o en el otro plazo.
- **Teclado** (con el mouse sobre la tarjeta, o después de tocarla; si no,
  las flechas siguen moviendo las cartas de análisis): ← → 6 h ·
  Shift+← → 24 h · Inicio / Fin · Espacio animar · 1–4 elegir carta ·
  ↑ ↓ cambiar carta · V cambiar vista. Animación lenta, normal o rápida. Desde el 29/10/2026 se **dibujan en el servidor** con
los datos del GFS de NOAA y el mismo formato que las de Alicia Bentley
(`app/meteo/cartas/estilo_bentley.py`): misma región (Mercator, 122,5°O a
12°E, 67°S a 12°N), mismos campos, intervalos y colores (leídos píxel por
píxel de las barras de sus imágenes). No llevan su firma: dicen "GFS 0,5°
(NOAA), procesado en el servidor del proyecto".

| Carta | Qué muestra |
|---|---|
| `mslp_jet` | presión a nivel del mar (cada 4 hPa), espesor 1000-500 (rojo ≥ 546, azul ≤ 540 dam), viento en 250 hPa (m/s) |
| `850_thetae` | altura de 850 hPa (cada 3 dam), θe (K, cada 6), viento (nudos) |
| `ivt` | altura de 700 hPa (cada 3 dam), IVT 1000-300 hPa (kg m⁻¹ s⁻¹) con sus vectores |
| `500_vort` | altura (cada 6 dam) y temperatura (cada 4 °C) en 500 hPa, ascenso (omega cada −0,5 Pa/s), vorticidad ciclónica, viento |

Por qué: el 28/10/2026 la página de Bentley copió la misma imagen vieja (del
20/10, +192 h) en todos los cuadros, y se guardó como si fuera el pronóstico.
Dibujándolo acá no dependemos de que su página ande.

- **Cuándo:** en la vuelta de cada hora se busca la corrida más nueva que ya
  tiene el +192 h en NOAA (sale ~5 h después de su hora de inicio). Si es más
  nueva que la guardada, se dibujan los 33 plazos: ~15 MB de NOAA y ~15-20 s
  por plazo, unos **15-25 minutos por corrida**, al final de la vuelta (no
  demora el resto). Cuatro corridas por día: ~2 GB por día de internet.
- **Solo la última corrida**, en `cartas/_pronostico/<carta>/` (`corrida.json`
  + una imagen por plazo, con la corrida en el nombre): ~80 MB fijos, no
  crece. La nueva se arma en una carpeta aparte y reemplaza a la anterior al
  terminar: nunca se ve a medias. Si algo falla en el medio, queda la
  anterior y se reintenta en la vuelta siguiente.
- Cada plazo que falla se reintenta a los 5 y 15 segundos.
- Para apagarlo: `activo: false` en las cuatro `gfs_pro_*` del YAML.
- Está también en la copia pública (`/api/cartas/pronostico`).

### El pronóstico de la página de Bentley (apagado)

Se puede seguir bajando el de su página (`pronostico: true`, o sacar la
línea, en las `bentley_*`): todos los plazos de su última corrida, con los
mismos controles que los análisis (`Last-Modified` posterior al inicio de la
corrida, reintentos, corridas publicadas a medias). Además, desde el
29/10/2026: un cuadro sin `Last-Modified` no se acepta, y si dos plazos traen
**exactamente la misma imagen**, se descartan (y una tanda repetida ya
guardada se borra sola). Lo mismo para los análisis: una carta idéntica a la
de otra hora no se archiva.

## Bentley (respaldo)

Si en algún momento se deja de bajarlas, `descargar: false` en las cuatro:
lo archivado se sigue viendo (solo donde hay imagen) y no se pide nada nuevo.
Así funciona su descarga:

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

Infrarrojo (canal 13), GeoColor, vapor de agua en tres alturas (canales 8, 9
y 10: 6,2, 6,9 y 7,3 µm), Air Mass (RGB de masas de aire) y rayos (GLM sobre
GeoColor) del GOES-19 para el sector "ssa" (sur de Sudamérica), 1800x1080, ya
coloreadas por NOAA (gratis). Solo a las horas de las cartas: ~28 imágenes por
día, unos 24 MB de internet y ~8,5 GB por año de disco. Los rayos están en la
carpeta del instrumento GLM (`instrumento: GLM` en `config/cartas.yaml`). NOAA guarda cada imagen con su fecha en el nombre
(`AAAADDDHHMM_GOES19-ABI-ssa-13-1800x1080.jpg`, con DDD = día del año) unos
pocos días: la tarea lee la lista de la carpeta y elige la más cercana a
cada hora sinóptica (±10 min), mirando 48 horas hacia atrás.

### Rayos propios (GLM sobre infrarrojo, solo con tormenta)

Además de la imagen de rayos de NOAA (todo el sur de Sudamérica), el servidor
arma un mapa propio de **Buenos Aires, Uruguay y el sur del Litoral**
(`satelite_rayos_bsas`): los destellos que vio el GLM en los **60 minutos
previos** a la hora de la carta, coloreados por antigüedad (amarillo = recién,
violeta = hace una hora), sobre el canal 13 en grises, con las ciudades del
grupo marcadas. Salen de los datos crudos del GLM (`GLM-L2-LCFA`, un archivo
cada 20 s) y del canal 13 (solo el pedazo de la región), del bucket público de
NOAA. Código: `app/meteo/cartas/rayos.py`.

**Solo cuando hay tormenta.** Cada mapa baja ~180 archivos (30–90 MB), así que
se arma únicamente si alguna estación sinóptica de la región informó tormenta
en el SYNOP (tiempo presente ww 13, 17, 29 o 91–99) entre una hora antes y una
hora después de la hora de la carta. Se revisa en cada vuelta las últimas 48 h,
porque el SYNOP puede llegar tarde. Ojo: hoy las sinópticas que se reciben son
solo las del SMN en Buenos Aires; una tormenta en Uruguay o Entre Ríos que no
llegue a ninguna de ellas no dispara el mapa (se pueden sumar estaciones de
allá en `estaciones.yaml`).

Junto a cada mapa se guarda la lista de destellos de la región en
`cartas/_rayos/<producto>/AAAA/MM/*.csv` (hora UTC, lat, lon, energía), para
contarlos o analizarlos después. Ocupa ~0,5 MB por mapa: unos pocos MB al año.

**Eventos pasados, a pedido.** NOAA guarda el GLM para siempre: el mapa de
cualquier fecha se puede armar después, aunque no haya habido SYNOP de
tormenta, con `--forzar`:

```bash
docker compose exec ingesta python -m meteo.ingesta historico cartas --desde 2026-10-20 --hasta 2026-10-21 --forzar
```

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

**Ogimet genera estos mapas también para fechas pasadas**, pero solo si el
pedido dice que viene de su página del formulario (encabezado `Referer:
https://www.ogimet.com/gsynop.phtml`, como manda el navegador); sin eso
responde vacío. Los del día ya los tiene armados y salen igual: por eso el
12/10/2026 se concluyó, mal, que "solo tenía el último día". Encontrado el
13/10 comparando el pedido del navegador (F12 → Red → Copiar como cURL) con
el de `curl`. En la vuelta de cada hora se completa
el último día; para rellenar un período:
`python -m meteo.ingesta historico cartas --desde ... --hasta ...`
(una consulta cada 21 s: ~1,5 minutos por día). Una respuesta vacía no se
reintenta: esa hora se saltea.

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
- **Tocar una estación** (mapas de Ogimet, visor grande, con la manito):
  muestra su nombre, número OMM, posición y valores, y un link a sus SYNOP.
  Al bajar cada mapa se guardan sus zonas clickeables (las mismas de la
  página de Ogimet) en `cartas/_areas/`. Para los mapas archivados antes:
  `historico cartas --desde ... --hasta ...` vuelve a pedir solo las páginas.
  El cartel de Ogimet trae solo el número OMM; el **nombre** sale de
  `app/meteo/cartas/estaciones_omm.csv` (~820 estaciones de Sudamérica y la
  Antártida, tomadas de Meteostat, licencia CC BY 4.0). Para las nuestras
  (`estaciones.yaml`) se usa el nombre de ahí. Si falta alguna o el nombre
  no gusta, se agrega o corrige una línea `omm;nombre;país` en ese archivo.
- **Cursor sincronizado** (placa): una cruz marca el mismo punto en las
  cartas de la misma proyección (las propias del GFS entre sí; las de Ogimet
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
- Debajo, **gráficos de lo observado ese día** (00 a 24 hs) en la estación
  sinóptica del SMN de la ciudad, con los SYNOP: temperatura y rocío (con la
  mínima y la máxima pronosticadas como líneas punteadas), presión, viento
  (velocidad y dirección) y lluvia por períodos. Salen de
  `/api/cartas/synop?estacion=omm_87593&fecha=AAAA-MM-DD`, que está también
  en la copia pública y por eso solo entrega estaciones sinópticas, nunca EMAs.

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
