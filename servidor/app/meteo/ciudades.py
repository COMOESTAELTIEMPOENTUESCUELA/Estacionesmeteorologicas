"""
Las ciudades que pronostica el grupo: ubicación y estación SYNOP (aeropuerto).
Lo usan el visualizador (mapas, panel del pronóstico) y la descarga de cartas
(valores del GFS en cada ciudad para el meteograma y los sondeos).
"""

# Ubicación (lat, lon). El panel del pronóstico muestra las que tienen algún
# pronóstico guardado: Bolívar, que hoy no se pronostica, aparece sola cuando
# el grupo empiece.
CIUDADES_COORD = {
    "La Plata": (-34.92, -57.95), "Junín": (-34.59, -60.95), "Mar del Plata": (-38.00, -57.56),
    "Bolívar": (-36.23, -61.11), "Tandil": (-37.32, -59.13), "Bahía Blanca": (-38.72, -62.27),
}

# Estación SYNOP (aeropuerto) de cada ciudad: la misma que usa la
# verificación (config/estaciones.yaml).
CIUDADES_SYNOP = {
    "La Plata": "omm_87593", "Junín": "omm_87548", "Mar del Plata": "omm_87692",
    "Bolívar": "omm_87640", "Tandil": "omm_87645", "Bahía Blanca": "omm_87750",
}

# Puntos donde se guardan los valores y el perfil vertical del GFS: las
# ciudades y Ezeiza, donde el SMN lanza el radiosondeo (para comparar el
# sondeo del modelo con el real).
PUNTOS_GFS = {**CIUDADES_COORD, "Ezeiza": (-34.82, -58.53)}
