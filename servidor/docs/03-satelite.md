# 3. Satélite GOES-19

> Módulo funcionando y probado con datos reales, pero **apagado por defecto**.
> Se activa con: `docker compose --profile satelite up -d`

## Cómo funciona

1. **De dónde:** NOAA publica gratis todas las imágenes del GOES-19 en
   Amazon S3 (`noaa-goes19`), unos minutos después de tomadas. Se usa el
   producto `ABI-L2-CMIPF`: disco completo cada 10 min, ya en magnitudes
   físicas (temperatura de brillo en K para las bandas infrarrojas,
   reflectancia para las visibles).
2. **Lectura parcial:** cada archivo trae medio planeta (24 MB la banda 13;
   377 MB la banda 2). Pero adentro está dividido en bloques con un índice.
   `remoto.py` pide por HTTP **solo los bytes** de los bloques que cubren
   nuestra región (header `Range`). Banda 13: ~3,5 MB en vez de 24 MB.
   Banda 2: ~16 MB en vez de 377 MB.
3. **Proyección:** el satélite ve ángulos, no lat/lon. `proyeccion.py` tiene
   las fórmulas de la guía oficial (GOES-R PUG, vol. 3, §4.2.8), verificadas
   contra `pyproj` en los tests.
4. **Se guarda:**
   - un **NetCDF** recortado con los valores originales **sin modificar**
     (+ bandera de calidad DQF por píxel): es el dato para análisis;
   - un **PNG** con mapa, para mirar.
5. **Índices JSON** por día (`index.json`) y de las últimas imágenes
   (`ultimas.json`) para el futuro visor web.

## Productos configurados (`config/satelite.yaml`)

| id | Banda | Región | Cada | Baja por imagen |
|---|---|---|---|---|
| ir13_centro | 13 (IR 10,3 µm) realzada | Centro-este | 10 min | ~3,5–5 MB |
| wv09_centro | 9 (vapor de agua medio) | Centro-este | 30 min | ~5 MB |
| vis02_laplata | 2 (visible 500 m) | La Plata | 20 min, solo de día | ~16 MB |
| ir13_laplata | 13 realzada | La Plata | 10 min | ~3 MB |

**Consumo de internet estimado:** ~1,5–2 GB/día. **Disco:** ~100 GB/año con
las retenciones por defecto (los PNG se borran después de 6–12 meses; los
NetCDF, que pesan menos, se guardan para siempre).

## Comandos

```bash
# Reconstruir un evento pasado (NOAA guarda todo):
docker compose run --rm satelite python -m meteo.satelite historico \
    --desde 2026-10-14T18:00 --hasta 2026-10-15T06:00 --productos ir13_centro,ir13_laplata

# Cambiaste una paleta? Regenerar los PNG desde los NetCDF, sin bajar nada:
docker compose run --rm satelite python -m meteo.satelite redibujar --desde ... --hasta ...
```

## Abrir un NetCDF para analizar

```python
import xarray as xr
ds = xr.open_dataset("ir13_laplata_20261010_1300.nc")
tb = ds.CMI - 273.15          # temperatura de brillo en °C
(tb < -50).sum()              # cuántos píxeles con topes muy fríos
```
