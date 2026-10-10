# Servidor meteorológico

Servidor casero (una PC vieja con Ubuntu) que **archiva en una sola base de
datos** todo lo que hoy está repartido entre ThingSpeak, Wunderground, la
Davis de la Facultad, Ogimet, las Google Sheets de la comunidad y los
pronósticos de Pronóstico UNLP. La idea es poder hacer estudios rigurosos:
series largas, metadatos de cada instrumento, control de calidad y nada que
se pierda cuando una plataforma borra su historial.

Las Google Sheets y las páginas actuales **siguen funcionando igual**: el
servidor solo lee y copia.

```
 FUENTES                  INGESTA (Python, cada 15-60 min)     BASE DE DATOS
 ThingSpeak ─────────┐                                         PostgreSQL + TimescaleDB
 Wunderground ───────┤    · convierte a UTC y unidades SI      ┌───────────────────────┐
 Davis FCAG ─────────┼──► · control de calidad (qc)       ───► │ observacion           │
 Ogimet (SYNOP) ─────┤    · guarda sin duplicar               │ estacion, instrumento │
 Sheet comunidad ────┤    · bitácora de errores               │ reporte_comunidad     │
 Pronóstico UNLP ────┘                                         │ pronostico_*  evento  │
                                                               └───────────────────────┘
 GOES-19 (NOAA, S3) ──► satélite: recorte + NetCDF + PNG ───► disco de 500 GB
```

## Estado

| Módulo | Estado |
|---|---|
| Base de datos (esquema, vistas, TimescaleDB) | ✅ Probado |
| EMAs ThingSpeak / Wunderground / Davis | ✅ Probado con datos de ejemplo — falta la primera corrida real en la PC |
| Sinópticas (Ogimet, decodificador SYNOP propio) | ✅ Probado con datos de ejemplo — falta la primera corrida real |
| Reportes de la comunidad (+ instrumento y período) | ✅ Probado con datos de ejemplo — falta la primera corrida real |
| Pronósticos (archivo de emisiones para verificación) | ✅ Probado con un pronóstico real |
| Satélite GOES-19 | ✅ Probado con datos reales (se activa aparte: `--profile satelite`) |
| Estación LPO | ⏸️ Pendiente (a decidir) |
| Visualizador web / API | ⏳ Próxima etapa |
| Modelos (GFS), cartas de superficie, radar | ⏳ Próxima etapa |

## Documentación

1. [Preparar la PC](docs/01-preparar-la-pc.md): Ubuntu, discos, Docker, backups.
2. [Los datos](docs/02-datos.md): fuentes, modelo de datos, consultas SQL y desde Python.
3. [Satélite](docs/03-satelite.md): cómo funciona el módulo GOES-19.

## Arranque rápido

```bash
cp .env.example .env && nano .env        # poner contraseña y rutas
docker compose up -d --build             # base de datos + ingesta
docker compose logs -f ingesta           # mirar que traiga datos
```

## Estructura

```
servidor/
├── docker-compose.yml      qué servicios corren y con qué configuración
├── .env.example            contraseñas y rutas (copiar a .env)
├── config/
│   ├── estaciones.yaml     estaciones, metadatos y fuentes  ← se edita acá
│   └── satelite.yaml       productos de satélite
├── db/init/01-esquema.sql  tablas y vistas
├── app/                    código Python (una sola imagen Docker)
│   └── meteo/
│       ├── ingesta/        recolectores, decodificador SYNOP, guardado
│       └── satelite/       GOES-19: proyección, lectura parcial, mapas
├── scripts/backup.sh       backup diario de la base
├── tests/                  pruebas automáticas
└── docs/
```

## Tests

```bash
docker compose run --rm -v $PWD/tests:/srv/app/tests -v $PWD/db:/srv/app/db \
    -v $PWD/config:/srv/app/config ingesta python -m pytest -q tests
```

(Los de base de datos necesitan `TEST_DATABASE_URL` apuntando a una base vacía; si no, se saltean.)
