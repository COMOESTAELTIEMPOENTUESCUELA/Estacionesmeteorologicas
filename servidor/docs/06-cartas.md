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
  plazo) · la misma carta en 2 plazos (12, 24 o 48 h antes o después) ·
  **consistencia entre corridas** (la misma hora pronosticada por las últimas
  4 corridas: si el modelo "va y viene", hay que confiar menos) ·
  **pronóstico contra lo que pasó** (el análisis de una hora al lado de lo que
  pronosticaba la corrida de 1 a 7 días antes; la barra recorre las horas con
  análisis). La vista elegida se recuerda en el navegador.
- **Corrida:** se puede elegir cualquiera de las guardadas (8 días).
- **Zoom** igual en todas las cartas a la vez: Ctrl + rueda (o la rueda sola
  si ya está acercada), doble clic (Shift + doble clic aleja), botones ＋ － ⟲,
  teclas + − 0; arrastrar para moverse. Se mantiene al cambiar de plazo.
- **Ciudades** del pronóstico del grupo como puntos (casillero "Ciudades" o
  tecla C); los nombres aparecen al acercar. Debajo de las cartas, la
  latitud y longitud del cursor. Los puntos se ubican con el "marco" que el
  servidor guarda en cada corrida (`marco` en corrida.json: dónde está el
  mapa en la imagen y una tabla latitud -> y de la proyección, sacada de
  cartopy para que coincida exactamente con lo dibujado).
- **Días:** botones por día ("vie 30") que llevan a las 9 hs (12 UTC) de ese día.
- **Cursor sincronizado** (el mismo casillero que el de las cartas de
  análisis): las 4 tienen el mismo marco, así que la cruz marca el mismo
  lugar en las otras cartas o en el otro plazo.
- **Teclado** (con el mouse sobre la tarjeta, o después de tocarla; si no,
  las flechas siguen moviendo las cartas de análisis): ← → 6 h ·
  Shift+← → 24 h · Inicio / Fin · Espacio animar · 1–4 elegir carta ·
  ↑ ↓ cambiar carta · V cambiar vista. Animación lenta, normal o rápida. Desde el 29/10/2026 se **dibujan en el servidor** con
los datos del GFS de NOAA y el mismo formato que las de Alicia Bentley
(`app/meteo/cartas/estilo_bentley.py`): misma región, mismos campos,
intervalos y colores (leídos píxel por píxel de las barras de sus imágenes)
y el **mismo marco**: imagen de 1160 x 912, el mapa en la misma caja (x 99 a
1060, y 49 a 788) y con los mismos límites en la proyección (Mercator, la
misma que cartopy: medida sobre las líneas de la grilla de sus cartas, con
residuos de 0,1 píxel). Por eso nuestras cartas de análisis y las de ella se
pueden **superponer con transparencia** en el visor (la clave `grupo:
bentley_southamer` del YAML las junta) y el cursor sincronizado marca el
mismo lugar en las dos. No llevan su firma: dicen "GFS 0,5°
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
- **Las corridas de los últimos 8 días**, en `cartas/_pronostico/<carta>/`:
  `corrida.json` (la última), `corridas/AAAAMMDDHH.json` (las anteriores) y
  una imagen por plazo y corrida (con la corrida en el nombre). Las más viejas
  se borran solas: **~800 MB fijos**, no crece. Las imágenes van con paleta de
  256 colores: ~180 KB cada una en vez de ~580 (no se nota la diferencia). La
  nueva se arma en una carpeta aparte y reemplaza a la anterior al terminar:
  nunca se ve a medias. Si algo falla en el medio, queda la anterior y se
  reintenta en la vuelta siguiente.
- **El +0 h (análisis) de cada corrida se archiva para siempre** como carta
  de análisis de esa hora (`gfs_ana_*` en el YAML, ~1 GB por año): se ve en
  las cartas de cada hora, al lado de las de Bentley.
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

## Orden de la pestaña Cartas

Arriba, tres botones; cada uno muestra solo su parte (se recuerda en el
navegador), con su color en la banda y en el borde de sus tarjetas:

1. **Pronóstico** (violeta): las cartas del GFS y el **meteograma del
   pronóstico** (el GFS en la ciudad, cada 6 h hasta +192 h: temperatura y
   rocío a 2 m, lluvia en 6 h, presión, viento a 10 m, 850 hPa, espesor,
   vientos en altura, agua precipitable y CAPE). La línea punteada del
   meteograma marca el plazo que se está mirando en las cartas.
2. **Diagnóstico** (verde): las cartas de análisis de cada hora, el
   diagnóstico por ciudad (meteograma de lo que pasó y corte tiempo-altura)
   y las observaciones de la hora. Las flechas del teclado mueven las cartas
   de análisis solo en esta sección.
3. **Pronóstico del grupo** (naranja): lo que pronosticó el grupo contra lo
   observado, con su propio selector de día.

Los valores del meteograma del pronóstico se guardan con cada corrida (los
mismos archivos que las cartas, más superficie, agua precipitable, CAPE y la
lluvia de las últimas 6 h): `cartas/_pronostico/_puntos/gfs_AAAAMMDDHH.json`,
8 días como las cartas. API: `/api/cartas/pronostico/punto?ciudad=[&corrida=]`.

En las cartas de análisis, los casilleros **"Mostrar:"** ocultan o muestran
cada grupo: Modelo (análisis del GFS y de Bentley), Satélite, Rayos (GLM) y
SYNOP (mapas de Ogimet). Se recuerda en el navegador. El grupo sale de la
`fuente` de cada carta (`GRUPOS_FUENTE` en index.html).

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

## Diagnóstico por ciudad (meteograma de "lo que pasó")

Tarjeta "Diagnóstico por ciudad" de la pestaña Cartas: para una ciudad (o
Ezeiza) y un período de 3, 5 o 10 días alrededor de la hora de la carta
(línea vertical punteada), en paneles con el mismo eje de tiempo:

- **Lo medido** por su estación del SMN (SYNOP, cada hora): temperatura,
  rocío, presión, viento (con barbas cada 3 h) y lluvia.
- **El análisis del GFS** en el punto de la ciudad, cada 6 h (punteado): T y
  Td a 2 m y presión (para comparar con lo medido), T, Td y θe en 850 hPa,
  espesor 1000-500, viento en 850, 500 y 250 hPa (con barbas), agua
  precipitable, IVT, CAPE y vorticidad en 500 hPa.

Arriba de los paneles, el **corte tiempo-altura** del GFS: los perfiles de
cada análisis uno al lado del otro (1000 a 200 hPa), con la humedad relativa
sombreada (70, 80, 90 y 95 %), la temperatura cada 5 °C (0 °C en azul;
isolíneas por "marching squares") y barbas en 925, 850, 700, 500, 300 y
250 hPa.

De dónde salen los valores del GFS: en cada análisis que se dibuja (las
cartas `gfs_analisis`) se guardan, además, los valores interpolados en cada
ciudad y en Ezeiza, y el perfil vertical de 1000 a 100 hPa (para los
sondeos): `app/meteo/cartas/puntos.py`, un JSON de ~7 KB por hora en
`cartas/_puntos/AAAA/MM/` (~10 MB por año). Se bajan unos 12 MB más por
análisis (los niveles del perfil). Para fechas viejas:
`historico cartas --desde ... --hasta ...` (si las cartas ya están, baja solo
lo de los puntos). API: `/api/cartas/punto?ciudad=&desde=&hasta=[&perfiles=true]`.

## Observaciones del día (todas)

En **Diagnóstico**, la tarjeta **Observaciones del día → 📋 Ver todas** muestra
cada medición del día de la carta (00 a 24 hs, hora argentina) de cada
estación de la ciudad elegida: la SYNOP del aeropuerto (una fila por mensaje,
como el parte del SMN; con el mouse sobre la hora se ve el mensaje original)
y las estaciones que usa la verificación (config/estaciones.yaml), por
ejemplo el Campbell y la Davis del Observatorio en La Plata (una fila por
medición, una columna por variable). Se pide recién al abrirla, una ciudad
por vez: `GET /api/cartas/observaciones-dia?ciudad=&fecha=`.

En la copia pública, las estaciones propias solo aparecen para quien entró
con su usuario; un visitante ve solo los SYNOP del SMN (datos públicos).
También se llega desde "Lo que pronosticamos": en el detalle de cada ciudad,
**📋 Todas sus observaciones del día**.

## Sondeo (sacado de la página)

El 02/11/2026 se sacó la tarjeta "Sondeo" (Skew-T del GFS y radiosondeo real
de Ezeiza): no hay datos como para verificarlo. Queda el código del servidor
(`sondeos.py`: lectura de IGRA e índices CAPE, CIN, PW, NCL, K, LI;
`/api/cartas/sondeo`) y se siguen guardando los perfiles del GFS (los usa el
corte tiempo-altura). Para bajar otra vez los radiosondeos reales,
descomentar `sondeos:` en `config/cartas.yaml`; el dibujo del Skew-T está en
la historia de git (commit edfdab0).

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

## Contexto climático (índices ENSO, SAM, MJO…)

Arriba de las cartas, una franja con el estado de los índices climáticos en
el día que se está mirando (en Diagnóstico y en Pronóstico del grupo, el de
la carta; en Pronóstico, hoy). "Ver evolución y período" muestra cada índice
con su curva del último año, el resumen de un período elegido (promedio, o
en la MJO cuántos días estuvo activa y en qué fase) y el diagrama de fases
de la MJO.

Tocando cualquier curva se abre **ampliada**, y se maneja como un mapa:
**ruedita** del mouse (o **pellizcar** en el celular, o los botones ＋ －) para
el zoom, **arrastrar** para moverse en el tiempo (o ◀ ▶ y las flechas del
teclado), y pasando el mouse o tocando un punto se ve su valor y fecha.
Abajo está la **serie entera** con un recuadro de lo que se ve: arrastrándolo
se va a otra época. Se cargan 10 años de los diarios y la serie completa de
los demás, y se arranca en el último año ("Ver: 6 meses … todo" cambia el
largo). Debajo, la lista de **cambios de fase** del tramo que se ve (cuándo
pasó de neutral a positivo, etc., con el umbral de cada índice); tocando una
fecha se va ahí.

| Índice | Qué mide | Frecuencia | Fuente |
|---|---|---|---|
| ONI | ENSO oficial: Niño 3.4, promedio de 3 meses (episodio = 5 trimestres seguidos pasando ±0,5 °C) | trimestral | NOAA CPC |
| Niño 3.4 y 1+2 | temperatura del mar en el Pacífico ecuatorial central y frente a Perú | semanal | NOAA CPC |
| SOI | presión Tahití − Darwin (parte atmosférica del ENSO) | mensual | NOAA PSL |
| SAM (AAO) | posición de los oestes del hemisferio sur | diario y mensual | NOAA CPC |
| MJO (RMM) | pulso de convección tropical: fase 1–8 y amplitud | diario | BoM (Australia) |
| DMI | dipolo del océano Índico | mensual | NOAA PSL (HadISST) |
| TNA, TSA y su diferencia | Atlántico tropical norte, sur y el dipolo del Atlántico tropical (TNA − TSA) | mensual | NOAA PSL |
| PDO | Pacífico norte, escala de décadas | mensual | NOAA NCEI |

Cada tarjeta tiene "Fuentes y para consultar": el archivo original que se
baja, la página oficial que explica el índice y, cuando corresponde, el
trabajo científico citado en los textos. También dice de dónde sale el
umbral de fase: el del ONI (NOAA), el del DMI (BoM) y la amplitud 1 de la MJO
(Wheeler y Hendon 2004) son los habituales; los demás (SAM, SOI, TNA, TSA,
dipolo del Atlántico, PDO, Niño 1+2) son un **criterio de esta página** para
pintar la franja, no una definición oficial. Los textos explicativos son un
resumen propio de esas fuentes. Están en `CONSULTAR` en `app/meteo/indices.py`.

Para leer más:
- ENSO: https://origin.cpc.ncep.noaa.gov/products/analysis_monitoring/ensostuff/ONI_v5.php
  y https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml
- SAM: https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/aao/aao.shtml
  y Silvestri y Vera (2003) https://doi.org/10.1029/2003GL018277
- MJO: http://www.bom.gov.au/climate/mjo/ , https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/mjo.shtml
  y Wheeler y Hendon (2004) https://doi.org/10.1175/1520-0493(2004)132<1917:AARMMI>2.0.CO;2
- Dipolo del Índico: https://psl.noaa.gov/gcos_wgsp/Timeseries/DMI/ y Chan, Behera y Yamagata (2008)
  https://doi.org/10.1029/2008GL034204
- Atlántico tropical, SOI y otros: https://psl.noaa.gov/data/climateindices/list/
- PDO: https://www.ncei.noaa.gov/access/monitoring/pdo/

La ingesta los baja cada 12 h (tarea `indices`) y guarda un JSON por índice
en `ARCHIVO_DIR/indices/`. Si una fuente falla, queda el último archivo
bueno. A mano:

```bash
docker compose exec ingesta python -m meteo.indices bajar     # baja todo y dice cuántos datos trajo de cada uno
docker compose exec ingesta python -m meteo.indices ver --fecha 2026-10-28
```

Ojo con las fechas: los mensuales y el ONI salen con demora (el dato de un
mes se publica a principios del siguiente), así que para "hoy" se muestra el
último publicado; si es demasiado viejo para la fecha elegida, aparece
punteado y dice "viejo para esta fecha". Código: `app/meteo/indices.py`.

## Tamaño de los centros A y B

Al elegir **A** o **B** en "Dibujar" aparece **Tamaño**: las letras nuevas
salen de ese tamaño (se recuerda en el navegador). Moverlo cambia, en vivo,
la última letra puesta; para cambiar una que ya está, tocarla con la A o la
B elegida y mover el control. Sirve para marcar el sistema principal más
grande que los secundarios.

## Sellos de tiempo presente y mover elementos

En "Dibujar": **☁ Símbolo** pone un sello de tiempo presente (niebla,
neblina, llovizna, lluvia, chaparrón, tormenta, nieve, granizo), del tamaño
del control **Tamaño**. **✥** mueve cualquier elemento del dibujo (A, B,
símbolo, texto, frente, trazo) arrastrándolo.

## Medir sobre las cartas (distancia, área, radio)

En el visor grande de una carta, la barra **Medir**:

- **📏 Distancia**: tocar puntos (varios tramos); doble clic, Enter o
  "✓ Terminar" para cerrar. Mide sobre la Tierra (círculo máximo), no en
  píxeles. Muestra el rumbo, la escala de Orlanski y:
  - como **longitud de onda** (de vaguada a vaguada): el número de onda
    zonal a esa latitud (2π·R·cos φ / λ);
- **🧭 Desplazamiento**: tocar dónde estaba un sistema y, en la carta de
  otra hora (+6 h, etc.: las medidas se conservan al cambiar de hora), dónde
  está ahora. Da cuánto se movió, hacia dónde y la velocidad (km/h y m/s).
  Las horas salen solas si las dos posiciones se marcaron en cartas de horas
  distintas; si no, se usan las del casillero. Avisa si da una velocidad
  irreal (más de 150 km/h).
- **⬠ Área**: contorno alrededor de un sistema → área (km²), eje mayor y
  menor, excentricidad y los criterios de tamaño de un **complejo
  convectivo de mesoescala** (Maddox 1980): escudo ≤ −32 °C ≥ 100.000 km²,
  parte ≤ −52 °C ≥ 50.000 km², excentricidad ≥ 0,7 (los alargados, 0,2–0,7,
  son los PECS de Anderson y Arritt 1998), y que dure ≥ 6 h (con ▶ Animar).
- **◎ Radio**: círculo de un radio dado alrededor de un punto.
- Arriba a la derecha, la **latitud y longitud** bajo el cursor.

Las medidas salen en el "⬇ PNG" y en la imagen que se pega en las notas
(pizarra), pero no se guardan con el dibujo.

**Coordenadas de cada carta** (`/api/cartas/georef`): las cartas estilo
Bentley (las propias del GFS y las de su página) usan el marco Mercator
exacto de `estilo_bentley.marco()`; los mapas propios de rayos, la caja del
mapa de `satelite/render.marco()`. Los **mapas de Ogimet se calibran
solos**: cada estación del mapa trae su cartel con la latitud y la longitud
exactas y el lugar (en píxeles) donde está dibujada; con el último mapa
guardado se ajustan por cuadrados mínimos las dos rectas (longitud → x;
latitud o Mercator → y, la que mejor dé) y se informa el error medio en km.
Las imágenes del satélite de NOAA STAR no traen ni proyección ni puntos: se
**calibran** una vez con **📍 Calibrar**, tocando 3 lugares conocidos y
lejos entre sí (mejor puntos de la costa). Con sesión, esa calibración queda
**guardada en el servidor para todos** (tabla `carta_calibracion`, con quién
la hizo); "Borrar calibración" la saca. También se puede fijar a mano con
`georef: {...}` en `config/cartas.yaml`, que tiene prioridad.
