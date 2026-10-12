# 5. Verificación de los pronósticos de Pronóstico UNLP

Compara cada pronóstico publicado por el grupo con lo que realmente pasó en
las estaciones. Se ve en la pestaña **Verificación** del visualizador (que
tiene un resumen de este documento en "Cómo funciona esta verificación") y
en `/api/verificacion` (JSON, o CSV con `&formato=csv`).

Contenido:
1. [Cómo funciona](#1-cómo-funciona)
2. [Qué datos se usan](#2-qué-datos-se-usan)
3. [Definiciones y parámetros](#3-definiciones-y-parámetros)
4. [Medidas de error](#4-medidas-de-error)
5. [Problemas que fueron surgiendo y cómo se resolvieron](#5-problemas-que-fueron-surgiendo-y-cómo-se-resolvieron)
6. [Limitaciones conocidas](#6-limitaciones-conocidas)
7. [Comandos](#7-comandos)
8. [Próximas etapas](#8-próximas-etapas)

---

## 1. Cómo funciona

```
 PRONÓSTICOS                                   OBSERVACIONES
 Apps Script de Pronóstico UNLP (cada hora)    SYNOP de Ogimet (SMN), EMAs (ThingSpeak, Davis)
 + historial de la hoja Verificacion_Pronostico          │
            │                                            ▼
            ▼                                   observacion  (cada dato, con su hora)
 pronostico_emision / pronostico_dia                     │
 (cada emisión, con sus correcciones)                    ▼  tarea "verificacion" (cada 3 h)
            │                                   verif_observado (lo observado por ciudad y día,
            │                                   en los mismos términos que el pronóstico)
            └──────────────┬─────────────────────────────┘
                           ▼
              /api/verificacion: pares pronóstico–observado
              + medidas de error por plazo + persistencia
                           ▼
                 pestaña Verificación
```

El trabajo se divide en tres pasos, cada uno en su archivo:

| Paso | Archivo | Qué hace |
|---|---|---|
| Lo observado | `meteo/verificacion/observado.py` | De las observaciones crudas arma, para cada ciudad y día, máxima, mínima, si llovió y el viento. |
| Lo pronosticado | `meteo/verificacion/pronostico.py` | Lleva el pronóstico a las mismas categorías (día con o sin precipitación, direcciones, intensidad). |
| La comparación | `meteo/verificacion/puntajes.py` | Arma los pares, elige la versión de cada emisión y calcula las medidas de error. |

La idea central: **pronóstico y observación tienen que estar en los mismos
términos**. Si el pronóstico dice "mínima del martes", hay que definir con
precisión qué es "la mínima observada del martes" (de qué estación, en qué
horario, con qué datos) antes de comparar.

## 2. Qué datos se usan

**Pronósticos.**
- Desde que el servidor está en marcha (10/10/2026): cada emisión que
  publica el grupo, capturada cada hora, con todos sus períodos y cada
  corrección como versión aparte.
- Antes: el historial de la hoja `Verificacion_Pronostico` (desde el
  12/08/2026), importado con `importar-hoja`. Trae el resumen que ya armaba
  el Apps Script, solo la versión vigente, y plazos 1 a 3.

**Observaciones, por ciudad** (en `config/estaciones.yaml`, sección
`verificacion`; cada lista es por prioridad: se usa la primera estación con
el día completo):

| Ciudad | Temperatura urbana | Temperatura suburbana | Lluvia | Viento |
|---|---|---|---|---|
| La Plata | Campbell (Observatorio); si falta, Davis | La Plata Aero (87593) | Aero, después Campbell, después Davis | Aero |
| Junín | Junín Aero (87548) | – | igual | igual |
| Mar del Plata | Mar del Plata Aero (87692) | – | igual | igual |
| Bolívar (sin pronósticos por ahora; desactivada en `estaciones.yaml`) | Bolívar Aero (87640) | – | igual | igual |
| Tandil | Tandil Aero (87645) | – | igual | igual |
| Bahía Blanca | Bahía Blanca Aero (87750) | – | igual | igual |

- En La Plata, la Davis no tiene anemómetro y Campbell está entre los
  árboles del Bosque: el viento se toma del Aero. Más adelante, LPO puede
  sumarse como urbana.
- En las otras ciudades la estación del aeropuerto hace de "urbana", igual
  que en la hoja del equipo.

## 3. Definiciones y parámetros

### El día

Es el **día civil, de 00 a 24 hs en hora argentina**, no el de 9 a 9 del
SMN. La mínima puede ser **nocturna**: por ejemplo, después de un frente
frío que pasa a la tarde, la temperatura sigue bajando hasta la medianoche.
Con el día civil, la mínima del martes es la más baja de todo el martes, sea
a las 6 o a las 23. La tabla día por día marca esos casos como "nocturna".

### Cómo se arma cada elemento

| Elemento | Observado | Pronosticado |
|---|---|---|
| **Máxima y mínima** | El mayor y el menor valor del día entre las lecturas (horarias del SYNOP, o cada pocos minutos en las EMAs), más los extremos que informa el SYNOP en sus grupos 1 y 2 (ver abajo). | `temp_max` / `temp_min` (urbana) y `_suburban` |
| **Día con precipitación** | EMA: 0,1 mm o más en el día. Sinóptica: algún reporte del día con tiempo presente de precipitación, o una cantidad mayor que 0 cuyo período cae al menos en un 75 % dentro del día. | Algún período con descripción de precipitación (lluvias, lloviznas, chaparrones, tormentas...), como en la hoja del equipo, sin importar la probabilidad. |
| **Dirección del viento** | El sector de 8 rumbos más frecuente en los valores horarios del día, sin contar las calmas. Si hay empate, el de más viento acumulado. | Todas las direcciones nombradas en los períodos del día. Acierta si la predominante observada está entre ellas. |
| **Intensidad del viento** | La categoría del mayor viento medio horario del día (no las ráfagas). | La mayor categoría nombrada ("leves a moderados" cuenta como MODERADOS). |

**Los extremos del SYNOP.** Los grupos 1 y 2 de la sección 3 traen la
máxima y la mínima de las **12 horas anteriores** al reporte:
- La máxima de las 00 UTC cubre de 09 a 21 hs: cae entera dentro del día y
  se usa siempre.
- La mínima de las 12 UTC cubre de 21 hs del día anterior a 09 hs: empieza 3
  horas antes del día. Se usa solo si es más baja que todas las lecturas de
  esas 3 horas de afuera, porque entonces seguro ocurrió dentro del día.
- Si de esas horas no hay lecturas (estaciones que no reportan de noche), no
  se puede comprobar. En ese caso se usa si la mayor parte de su período cae
  dentro del día, que es la convención del SMN.

**Tiempo presente (ww) que cuenta como precipitación:** 20 a 27 (en la
última hora) y 50 a 97 y 99 (en el momento). No cuentan 28 (niebla) ni 29 y
98 (tormenta sin precipitación). Así, una llovizna que deja solo traza
(inapreciable, que se guarda como 0 mm) igual cuenta como día con
precipitación, igual que un pronóstico de "lloviznas".

### Parámetros

| Parámetro | Valor | Para qué |
|---|---|---|
| Día completo | datos en 18 de las 24 horas (75 %) | Si no, el día no entra en la cuenta (ni acierto ni error). |
| Día completo para el viento | 8 horas | Hay estaciones del SMN que solo reportan de día. |
| Umbral de lluvia en EMAs | 0,1 mm | Menos que eso es "sin precipitación". |
| Superposición de una cantidad de lluvia con el día | 75 % | Para que la lluvia de 24 h de las 9 (de 9 a 9) no se asigne entera a un día. |
| Período de los extremos del SYNOP | 12 h antes del reporte | Convención de la OMM para 00 y 12 UTC. |
| Margen para comprobar un extremo | 0,05 °C | El extremo tiene que ser más bajo (o alto) que las lecturas de afuera por al menos esto. |
| Calma | viento menor a 0,5 m/s (unos 2 km/h) o dirección 0 | No cuenta para la dirección predominante. |
| Categorías de viento (del equipo) | LEVES ≤ 22 km/h, MODERADOS ≤ 29, REGULARES ≤ 39, FUERTES más | Las mismas que usan los importadores del grupo. |
| Plazos | 0 (mismo día) a 3 | La hoja del equipo tiene 1 a 3; el servidor captura también el 0. |

Además de los valores del día se guarda el detalle **por período**
(madrugada 00-06, mañana 06-12, tarde 12-18 y noche 18-24) en
`verif_observado.detalle`: es la base para verificar por período más
adelante.

### Plazo, versión y publicación

- **Plazo.** Cuenta desde el día del pronóstico: su "Día 2" es plazo 1, su
  "Día 3" plazo 2, y así.
- **Publicado la noche anterior.** El grupo puede publicar el pronóstico de
  mañana la noche anterior. El plazo se cuenta igual que para el resto, así
  se pueden comparar los pronosticadores entre sí. Esas emisiones quedan
  marcadas ("noche ant.") y el filtro **Publicación** permite separarlas,
  porque su antelación real es un día más. El día de publicación sale de la
  hoja (`publicado_el`) o del momento en que el servidor capturó la emisión.
- **Versión.** Si una emisión se corrigió, "final" usa la corrección y
  "original" lo primero que se publicó. Comparar las dos muestra si las
  correcciones mejoran el pronóstico. En el historial importado de la hoja
  son iguales, porque la hoja solo guardaba la versión vigente.

### Controlar un dato en Ogimet

Cada día de la tabla día por día (y de la lista de mínimas nocturnas) tiene
un link "Ogimet NNNNN ↗" por cada estación sinóptica usada ese día. Abre la
tabla de SYNOP decodificados de Ogimet hasta las 12 UTC del día siguiente,
con 2 días hacia atrás: se ve todo el día civil más el reporte que trae la
mínima de esa noche. **Ogimet muestra las horas en UTC** (3 más que la hora
argentina). Si el día todavía no terminó, el link corta en la hora actual
(Ogimet responde "400 Bad Request" si se le pide una hora futura). En la
pestaña Estaciones SMN, "Ver en Ogimet" abre la misma estación y período.

### Mínimas nocturnas

La pestaña cuenta, para la mínima urbana y la suburbana, en qué período del
día ocurrió la mínima (madrugada 00-06, mañana 06-12, tarde 12-18, noche
18-24). También lista los días con mínima nocturna, con la mínima de la
madrugada de ese mismo día y lo pronosticado en cada plazo, y compara el
error de la mínima en esos días contra el resto. En las estaciones que no
reportan de noche (Bolívar, Tandil, Dolores) el conteo es un mínimo: una
mínima nocturna puede no detectarse.

### Etiquetas de situación

Salen de los SYNOP horarios de la estación del SMN de cada ciudad (en La
Plata, el Aero) y se guardan como elemento `etiquetas` en `verif_observado`,
con la **evidencia** de cada una (por qué se puso) y la lista de las que no
se pudieron evaluar por falta de datos. Código: `meteo/verificacion/etiquetas.py`
(los umbrales están en `UMBRALES`).

| Etiqueta | Criterio |
|---|---|
| Paso de frente frío | En 6 h: presión +3 hPa o más, temperatura −4 °C o más, y el viento pasa al cuadrante sur (SE a SW) |
| Noche radiativa | De 21 a 07 hs: nubosidad ≤ 2 octas y viento < 2 m/s en el 70 % de las horas |
| Viento norte (advección cálida) | 6 h o más de viento del N o NE de 3 m/s o más |
| Sudestada | 6 h o más de viento del E o SE de 30 km/h o más |
| Día cubierto | Nubosidad media ≥ 7 octas de 09 a 18 hs |
| Niebla | Tiempo presente 40-49 o visibilidad < 1 km |
| Tormenta | Tiempo presente 17, 29 o 91-99 |
| Lluvia intensa | 20 mm o más en 24 h (reporte de las 12 UTC del día siguiente) |
| Viento fuerte | Viento medio horario de 40 km/h o más |
| Helada / helada agrometeorológica | Mínima ≤ 0 °C / ≤ 3 °C en el abrigo |
| Caída fuerte de presión | −8 hPa o más en 24 h (indicador local; la ciclogénesis de verdad necesita el mapa de presión de la región) |
| Mínima nocturna | La mínima ocurrió entre las 18 y las 24 hs |

En la pestaña: el filtro **Situación** muestra todas las medidas solo para
esos días; la tabla **Errores según la situación** compara cada situación
con el resto de los días; y los días se pueden **ordenar por error**.

### Semáforo de confiabilidad e intervalos

- **Semáforo**: exploratorio (menos de 30 casos), indicativo (30 a 99),
  robusto (100 o más). Para la lluvia cuentan los días **con lluvia**:
  menos de 10, exploratorio; 30 o más (y 100 días), robusto.
- **Intervalo del 90 %** por *bootstrap*: se rearma la muestra al azar 1000
  veces (días elegidos con repetición) y se toma el rango central de los
  resultados. Semilla fija: la misma consulta da siempre el mismo rango.
- **Comparaciones** (una situación contra el resto, nocturnas contra el
  resto, pronóstico contra persistencia en los mismos días): se calcula el
  intervalo de la diferencia; si incluye el 0, "la diferencia puede ser azar".

## 4. Medidas de error

**Temperatura**, con e = pronóstico − observado:

| Medida | Cálculo | Cómo se lee |
|---|---|---|
| MAE | promedio de \|e\| | Cuánto se erra en promedio, sin signo. 0 = perfecto. |
| Sesgo | promedio de e | + = se pronostica de más; − = de menos. Un sesgo estable se puede corregir. |
| RMSE | raíz del promedio de e² | Como el MAE, pero castiga los errores grandes. Si es mucho mayor que el MAE, hubo días con errores muy grandes. |
| ±2° | % de días con \|e\| ≤ 2 | Proporción de días "buenos". |

**Precipitación sí/no**, con una tabla de contingencia:

|  | Llovió | No llovió |
|---|---|---|
| **Se anunció** | a = acierto | b = falsa alarma |
| **No se anunció** | c = sorpresa | d = día seco acertado |

| Medida | Cálculo | Ideal | Cómo se lee |
|---|---|---|---|
| POD | a / (a + c) | 1 | De los días que llovió, qué fracción se había anunciado. |
| FAR | b / (a + b) | 0 | De los días que se anunció lluvia, en qué fracción no llovió. |
| CSI | a / (a + b + c) | 1 | Aciertos sin contar los días secos, que son "fáciles". |
| BIAS | (a + b) / (a + c) | 1 | Mayor que 1: se anuncia lluvia más seguido de lo que llueve. |
| % acertados | (a + d) / n | 1 | Engañoso si casi nunca llueve: decir siempre "no" ya da alto. |
| HSS (Heidke) | 2(ad − bc) / [(a+c)(c+d) + (a+b)(b+d)] | 1 | Aciertos por encima de los que se lograrían por azar. 0 = sin valor, negativo = peor que el azar. |

**Viento**: % de aciertos de dirección, % de intensidad exacta y % con una
categoría de diferencia o menos.

**Referencia: persistencia.** Es pronosticar "lo mismo que se observó el día
antes de la emisión". Un MAE de 2° no dice nada por sí solo; comparado con
la persistencia, sí. La **destreza** es 1 − MAE / MAE de la persistencia,
calculada sobre los mismos días: 30 % quiere decir que el pronóstico erra un
30 % menos que la persistencia; negativa, que erra más.

**n** es la cantidad de días que entraron en cada cálculo. Un día entra solo
si hay pronóstico **y** observación completa. Con pocos días (menos de 30,
digamos), uno o dos días malos mueven mucho los promedios.

## 5. Problemas que fueron surgiendo y cómo se resolvieron

En orden de aparición. Cada uno quedó con un test automático que lo
reproduce, para que no vuelva a pasar.

| # | Problema | Cómo se vio | Causa | Solución |
|---|---|---|---|---|
| 1 | La tabla del visualizador "no coincidía" con Ogimet en la hora | Una observación de las 09:00 en el visualizador aparecía a las 12:00 en Ogimet | El visualizador muestra hora argentina; Ogimet, UTC (3 horas más). Los datos eran los mismos. | Se muestra la hora UTC al lado de la hora argentina. |
| 2 | La mínima de la hoja vieja parecía estar asignada al día equivocado | El script de la hoja asigna al día X la mínima que termina a las 09 hs del día X+1 | Usa el reporte de las 12 UTC del día siguiente, que es la madrugada de X+1 | La verificación del servidor usa el día civil (punto 3). **Pendiente de confirmar con los datos reales.** |
| 3 | La mínima puede ser nocturna | Planteado por el grupo: después de un frente frío a la tarde, la mínima del día ocurre a la noche | Con el día de 9 a 9, esa mínima se le asignaría al día siguiente | Día civil 00-24; la mínima de las 12 UTC del SYNOP solo se usa si se comprueba que ocurrió dentro del día. |
| 4 | La lluvia de 24 h se informa de 9 a 9 | Un día civil queda repartido entre dos reportes de 24 h | El reporte de las 12 UTC acumula de 09 hs del día anterior a 09 hs | Una cantidad cuenta como "llovió" solo si su período cae al menos 75 % dentro del día. El tiempo presente de cada hora completa la información. |
| 5 | En 48 filas de la hoja el plazo no coincidía con las fechas | fecha pronosticada − fecha de emisión = plazo + 1 | Eran 16 pronósticos publicados la noche anterior | Se usa el plazo del bloque para todos; esas emisiones quedan marcadas y se pueden filtrar. |
| 6 | La Plata Aero no se descargó | `504 Server Error: Gateway Time-out` en una semana | Ogimet sobrecargado; una semana fallida descartaba la estación entera | Cada semana se reintenta (30 y 90 s); si sigue fallando, se avisa cuál quedó sin traer y se sigue con las demás. |
| 7 | Bolívar, Tandil y Dolores tenían muchos días sin dato | Unas 4.500 a 7.800 observaciones contra 13.000 de las otras | No reportan de noche (mandan `NIL`); la regla de 18 horas los dejaba afuera | Máxima y mínima validadas con los grupos del SYNOP; día seco con lluvias acumuladas en cero; viento con 8 horas. |
| 8 | La Plata urbana (Campbell) tenía solo 27 de 62 días | Consulta de cobertura | Con más de 8000 registros en el rango, ThingSpeak devuelve los **más recientes**; el código suponía los primeros y cortaba | Se pagina hacia atrás hasta que una tanda viene incompleta. Ahora 62 de 62. |
| 9 | Bolívar tenía lluvia en solo 15 de 62 días | Mensajes reales del 10/08: `31960` a las 12 UTC, `NIL` de noche | El indicador iR = 3 ("grupo de lluvia omitido por ser cero") no se leía, así que la noche quedaba sin información | iR = 3 vale 0 mm (24 h a las 12 UTC; 6 h a las 00, 06 y 18 UTC). Comando `reprocesar-synop` para volver a leer lo guardado sin pedir nada a Ogimet. Ahora 60 de 62. |
| 10 | El pronóstico del viernes 9/10 de La Plata no aparecía en ningún lado | El portal mostró el del jueves todo el viernes; en la hoja "Pronosticos", la fila decía 10/10 pero se guardó el 9/10 a las 08:14 | Publicado a la mañana con "CREAR PARA MAÑANA (Turno noche)" tildado: TODAS las fechas corridas un día. Revisando la hoja completa: 8 casos así | `config/correcciones_pronosticos.yaml` (registro) + `aplicar-correcciones`: se corrigen las fechas en el lugar, anotando el motivo. "Publicado el día anterior" no siempre es turno noche: hay que mirar la HORA. |

Lección general: **revisar siempre la cobertura** (cuántos días hay de cada
cosa) antes de mirar los resultados. Los problemas 6 a 9 aparecieron así, y
ninguno daba error: solo faltaban datos en silencio. El 10 tampoco daba
error: los datos estaban, pero con la fecha equivocada.

## 6. Limitaciones conocidas

- **Viento de Bolívar: 28 de 62 días.** Algunos días la estación manda muy
  pocos reportes (2 o 3) y con eso no se puede decir cuál fue la dirección
  predominante. Se dejan afuera antes que inventar un dato. Igual, en el
  historial de la hoja no hay pronósticos de Bolívar.
- **Extremos entre horas.** Con lecturas horarias, la máxima o la mínima
  real puede caer entre dos horas. Se compensa con los grupos 1 y 2 del
  SYNOP, pero no siempre se pueden usar (punto 3).
- **El historial de la hoja** no tiene los períodos ni las correcciones, así
  que la verificación por período y la comparación original/final solo
  valen desde que el servidor captura en vivo.
- **La regla de "día con precipitación"** del pronóstico no mira la
  probabilidad: "10 a 30 % de lluvias aisladas" cuenta como "sí", igual que
  en la hoja del equipo. Eso infla las falsas alarmas; la verificación por
  probabilidad (próxima etapa) lo va a tratar mejor.

## 7. Comandos

```bash
# Importar el historial de la hoja (descargada como CSV; se puede repetir, no duplica):
docker compose exec -T ingesta python -m meteo.ingesta importar-hoja - < /srv/meteo/verificacion.csv

# Mejor: el historial COMPLETO de la hoja "Pronosticos" (con períodos, Día 1,
# probabilidades). Aplica las correcciones de fecha y reemplaza lo resumido:
docker compose exec -T ingesta python -m meteo.ingesta importar-pronosticos - < pronosticos.csv
# Corregir fechas anotadas en config/correcciones_pronosticos.yaml:
docker compose exec ingesta python -m meteo.ingesta aplicar-correcciones
# Traer observaciones de un período:
docker compose exec ingesta python -m meteo.ingesta historico ogimet --desde 2026-08-10 --hasta 2026-10-13
docker compose exec ingesta python -m meteo.ingesta historico thingspeak --desde 2026-08-10 --hasta 2026-10-13 --estaciones observatorio_campbell

# Volver a decodificar los SYNOP guardados (después de mejorar el decodificador):
docker compose exec ingesta python -m meteo.ingesta reprocesar-synop --desde 2026-08-01

# Recalcular lo observado (siempre al final, con todo ya cargado):
docker compose exec ingesta python -m meteo.ingesta historico verificacion --desde 2026-08-10 --hasta 2026-10-13

# Revisar la cobertura:
docker compose exec -T db psql -U meteo -d meteo -c "SELECT ciudad, elemento, count(*) dias, string_agg(DISTINCT estacion_id, ', ') estaciones FROM verif_observado GROUP BY 1, 2 ORDER BY 1, 2"
```

En el funcionamiento normal no hace falta nada de esto: el servicio captura
los pronósticos cada hora y recalcula lo observado de los últimos 5 días
cada 3 horas.

## 8. Próximas etapas

1. Verificar por período (madrugada, mañana, tarde, noche) y por probabilidad
   de precipitación: puntaje de Brier y diagrama de confiabilidad.
2. Comparar con modelos numéricos (GFS / ECMWF) para medir cuánto valor
   agrega el pronosticador.
3. Sumar la estación LPO como urbana de La Plata.
