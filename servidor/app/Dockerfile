# Una sola imagen para todos los servicios de Python (ingesta, satélite...):
# cambia solo el comando con que se arranca cada contenedor.
FROM python:3.12-slim

# Todo en UTC adentro del contenedor: la hora local se calcula al mostrar.
ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CARTOPY_DATA_DIR=/opt/cartopy

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Costas, países y provincias (Natural Earth) para los mapas del satélite.
# Se bajan una vez al construir la imagen; si falla, se bajan al primer uso.
RUN python -c "import cartopy.feature as f; \
[list(x.geometries()) for x in (f.COASTLINE.with_scale('10m'), f.BORDERS.with_scale('10m'), \
 f.NaturalEarthFeature('cultural','admin_1_states_provinces_lines','10m'))]" || true

COPY meteo ./meteo

# Usuario sin privilegios: si algo sale mal, no es root.
RUN useradd --uid 1000 --create-home meteo && chown -R meteo /opt/cartopy 2>/dev/null || true
USER meteo
