# 5. Verificación de los pronósticos de Pronóstico UNLP

Compara cada pronóstico publicado con lo que pasó en las estaciones. La
pestaña **Verificación** del visualizador lo muestra; `/api/verificacion`
devuelve los mismos datos (o un CSV con `&formato=csv`).

## Cómo funciona

```
pronostico_emision / pronostico_dia      (cada emisión que publica el grupo, con sus correcciones)
                │
observacion ──► verif_observado          (tarea "verificacion", cada 3 h, últimos 5 días)
                │
                ▼
      /api/verificacion  ──►  pares pronóstico–observado + medidas de error, por plazo
```

- `meteo/verificacion/observado.py`: lo observado de cada día.
- `meteo/verificacion/pronostico.py`: lo pronosticado, llevado a las mismas categorías.
- `meteo/verificacion/puntajes.py`: los pares y las medidas de error.
- `config/estaciones.yaml`, sección `verificacion`: qué estaciones representan cada ciudad.

## Definiciones (primera etapa)

**El día** es el día civil, de 00 a 24 hs en hora argentina. No se usa el de
9 a 9 porque la mínima puede ser **nocturna**: por ejemplo, después de un
frente frío que pasa a la tarde, la temperatura sigue bajando hasta la
medianoche. Con el día civil, la mínima del martes es la más baja de todo el
martes. La tabla día por día marca esos casos como "nocturna".

| Elemento | Observado | Pronosticado |
|---|---|---|
| Máxima y mínima | Extremos de las lecturas del día. Para las sinópticas se suman los grupos 1 y 2 del SYNOP; el de las 12 UTC (de 21 a 09 hs) solo se usa si es seguro que ocurrió dentro del día. | `temp_max`, `temp_min` (urbana) y `_suburban` |
| Día con precipitación | EMA: 0,1 mm o más. Sinóptica: algún reporte del día con tiempo presente (ww) de precipitación, o una cantidad mayor que 0 cuyo período cae al menos en un 75 % dentro del día. | Algún período con descripción de precipitación (como la hoja del equipo) |
| Dirección del viento | Sector (8 rumbos) más frecuente en los valores horarios, sin contar las calmas. | Todas las direcciones nombradas en los períodos |
| Intensidad del viento | Categoría del mayor viento medio horario. LEVES ≤ 22 km/h, MODERADOS ≤ 29, REGULARES ≤ 39, FUERTES más. Son los umbrales del equipo. | La mayor categoría nombrada ("leves a moderados" cuenta como MODERADOS) |

Un día cuenta como observado solo si hay datos en al menos 18 de sus 24
horas. Hay excepciones para las estaciones del SMN que no reportan de noche
(Bolívar, Tandil, Dolores):
- la máxima y la mínima valen si las respalda el grupo correspondiente del SYNOP;
- el día seco vale si las lluvias acumuladas en 0 cubren al menos 18 horas.
  Cuenta también el indicador iR = 3 del SYNOP ("grupo de lluvia omitido por
  ser cero"): a las 12 UTC vale 0 mm en 24 h y a las 00, 06 y 18 UTC 0 mm en 6 h;
- para el viento alcanza con 8 horas.

Después de cambiar el decodificador, los mensajes ya guardados se vuelven a
leer sin pedirle nada a Ogimet con `python -m meteo.ingesta reprocesar-synop`. Si no, no entra en la cuenta: no suma como acierto ni como error.

Además de los valores del día se guarda el detalle **por período**
(madrugada, mañana, tarde, noche) en `verif_observado.detalle`. Es la base
para la segunda etapa: verificar por período y por probabilidad.

**Estaciones por ciudad.** La Plata urbana usa Campbell y, si falta, Davis
(más adelante se sumará LPO). La Plata suburbana es el Aero. En las otras
ciudades se usa el aeropuerto, igual que en la hoja del equipo.

**Publicado la noche anterior.** El equipo puede publicar el pronóstico de
mañana la noche anterior. El plazo se cuenta igual para todos, desde el día
del pronóstico: el "Día 2" es siempre plazo 1. Así se pueden comparar los
pronosticadores entre sí. Esas emisiones quedan marcadas (`anticipado`) y el
filtro **Publicación** las separa. Sirve para ver si hay diferencias, porque
su antelación real es un día más. El día de publicación sale de la hoja
(`publicado_el`) o del momento en que el servidor capturó la emisión.

**Versión.** Si una emisión se corrigió, `final` usa la corrección y
`original` lo primero que se publicó. Comparar las dos muestra si las
correcciones mejoran el pronóstico.

## Medidas de error

- **Temperatura**:
  - MAE: error medio sin signo.
  - Sesgo: error medio con signo; + quiere decir que se pronostica de más.
  - RMSE: como el MAE, pero castiga más los errores grandes.
  - % de días con error de 2° o menos.
- **Precipitación sí/no**: se arma una tabla de contingencia con:
  - a = aciertos (se anunció y llovió);
  - b = falsas alarmas (se anunció y no llovió);
  - c = sorpresas (llovió sin anunciarse);
  - d = días secos acertados.

  De esa tabla salen:
  - POD = a/(a+c)
  - FAR = b/(a+b)
  - CSI = a/(a+b+c)
  - BIAS = (a+b)/(a+c)
  - HSS: aciertos por encima del azar.
- **Viento**: % de aciertos de dirección, % de intensidad exacta y % con una
  categoría de diferencia o menos.
- **Referencia: persistencia.** Es pronosticar "lo mismo que se observó el día
  antes de la emisión". La **destreza** se calcula como
  1 − MAE / MAE de la persistencia, sobre los mismos días. Un pronóstico
  aporta valor si le gana a la persistencia.

## Comandos

```bash
# Recalcular lo observado de un período (por ejemplo, después de rellenar Ogimet):
docker compose exec ingesta python -m meteo.ingesta historico ogimet --desde 2026-01-01 --hasta 2026-10-01
docker compose exec ingesta python -m meteo.ingesta historico verificacion --desde 2026-01-01 --hasta 2026-10-01
```

## Importar el historial de la hoja del equipo

El servidor guarda las emisiones recién desde que se puso en marcha. Lo
anterior está en la hoja `Verificacion_Pronostico`: se descarga como CSV
(Archivo → Descargar → CSV, con esa pestaña abierta) y se importa así:

```bash
docker compose exec -T ingesta python -m meteo.ingesta importar-hoja - < Verificacion_Pronostico.csv
```

- Se puede repetir con una versión más nueva del CSV: lo que no cambió no se
  duplica. Si una fila cambió (se corrigió), entra como versión nueva y
  "final" usa la más reciente.
- Las emisiones que el servidor ya capturó en vivo no se importan: esas
  tienen los períodos completos.
- La hoja guarda lo ya resumido (SI/NO, "W, SW", "LEVES, MODERADOS") y solo
  la versión vigente, sin plazo 0. Por eso, en el historial importado,
  "original" y "final" son iguales.
- **Plazo**: se respeta el de la hoja, que es el bloque del pronóstico (Día 2
  = plazo 1). Si alguien publicó la noche anterior, la fecha real de
  publicación queda en `contenido->'publicado_el'`.

Después hay que traer las observaciones de esas fechas y recalcular:

```bash
docker compose exec ingesta python -m meteo.ingesta historico ogimet --desde 2026-08-10 --hasta 2026-10-13
docker compose exec ingesta python -m meteo.ingesta historico thingspeak --desde 2026-08-10 --hasta 2026-10-13 --estaciones observatorio_campbell
docker compose exec ingesta python -m meteo.ingesta historico verificacion --desde 2026-08-10 --hasta 2026-10-13
```

## Próximas etapas

1. Verificar por período (madrugada, mañana, tarde, noche) y por probabilidad
   de precipitación: puntaje de Brier y diagrama de confiabilidad.
2. Comparar con modelos numéricos (GFS / ECMWF) para medir cuánto valor
   agrega el pronosticador.
3. Sumar la estación LPO como urbana de La Plata.
