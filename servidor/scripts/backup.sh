#!/usr/bin/env bash
# Backup diario de la base de datos.
#
# pg_dump con formato "custom" (-Fc): comprimido, y permite restaurar tablas
# sueltas. Se guardan los últimos 14 días y, además, uno por mes para siempre
# (el del día 1).
#
# Restaurar (procedimiento probado; TimescaleDB necesita los pasos pre/post):
#   docker compose exec -T db psql -U meteo -d postgres -c "CREATE DATABASE restaurada"
#   docker compose exec -T db psql -U meteo -d restaurada -c "CREATE EXTENSION timescaledb; SELECT timescaledb_pre_restore();"
#   docker compose exec -T db pg_restore -U meteo -d restaurada < meteo_AAAA-MM-DD.dump
#   docker compose exec -T db psql -U meteo -d restaurada -c "SELECT timescaledb_post_restore();"
#
# Las advertencias de pg_dump sobre "circular foreign-key constraints" en
# tablas internas de TimescaleDB (hypertable, chunk) son normales.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a
DESTINO="${BACKUP_DIR:-./datos/backups}"
mkdir -p "$DESTINO/diarios" "$DESTINO/mensuales"

HOY=$(date +%Y-%m-%d)
ARCHIVO="$DESTINO/diarios/meteo_$HOY.dump"

# Las advertencias van a un archivo aparte; si pg_dump FALLA, se muestran.
if ! docker compose exec -T db pg_dump -U meteo -d meteo -Fc > "$ARCHIVO.tmp" 2> "$ARCHIVO.err"; then
    echo "$(date -Is) ERROR en el backup:" >&2
    cat "$ARCHIVO.err" >&2
    rm -f "$ARCHIVO.tmp"
    exit 1
fi
rm -f "$ARCHIVO.err"
mv "$ARCHIVO.tmp" "$ARCHIVO"
echo "$(date -Is) backup OK: $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"

if [ "$(date +%d)" = "01" ]; then
    cp "$ARCHIVO" "$DESTINO/mensuales/"
fi

# Borrar diarios de más de 14 días.
find "$DESTINO/diarios" -name 'meteo_*.dump' -mtime +14 -delete
