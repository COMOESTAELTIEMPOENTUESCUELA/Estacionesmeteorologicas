"""
Buscar archivos del GOES en el bucket público de NOAA en Amazon S3.

NOAA publica todo lo que produce el GOES-19 (en tiempo casi real, ~5-10 min de
demora) en https://noaa-goes19.s3.amazonaws.com/ , gratis y sin registrarse.
Los archivos se organizan así:

    ABI-L2-CMIPF / 2026 / 283 / 12 / OR_ABI-L2-CMIPF-M6C13_G19_s20262831200215_e..._c....nc
    └─ producto    └año  └día  └hora └─ nombre: banda 13, comienzo del escaneo 2026, día 283,
                          juliano UTC            12:00:21.5

- ABI   = Advanced Baseline Imager (la cámara del satélite)
- L2    = "nivel 2": ya convertido a magnitud física
- CMIP  = Cloud and Moisture Imagery Product: reflectancia (bandas 1-6) o
          temperatura de brillo en Kelvin (bandas 7-16)
- F     = Full disk (disco completo, cada 10 min). C = CONUS (no cubre
          Argentina), M = mesoescala (sectores móviles de 1 min)
- M6    = modo de escaneo 6

No usamos boto3 (la librería oficial de Amazon): la API de listado de S3 es un
GET común que devuelve XML, y así se ve claro qué está pasando.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
PATRON = re.compile(
    r"OR_(?P<producto>ABI-L2-[A-Z]+)-M(?P<modo>\d)C(?P<banda>\d{2})_(?P<sat>G\d{2})_s(?P<inicio>\d{14})"
)


@dataclass(frozen=True)
class ArchivoGOES:
    clave: str  # ruta dentro del bucket
    banda: int
    satelite: str  # G19
    inicio: datetime  # comienzo del escaneo, UTC
    tam: int

    def url(self, bucket):
        return f"https://{bucket}.s3.amazonaws.com/{self.clave}"


def parsear_inicio(s14):
    """'20262831200215' -> datetime UTC. Formato: AAAA JJJ HH MM SS d (décimas)."""
    anio, dia, hh, mm, ss = int(s14[0:4]), int(s14[4:7]), int(s14[7:9]), int(s14[9:11]), int(s14[11:13])
    return datetime(anio, 1, 1, hh, mm, ss, tzinfo=timezone.utc) + timedelta(days=dia - 1)


def parsear_clave(clave, tam=0):
    m = PATRON.search(clave)
    if not m:
        return None
    return ArchivoGOES(
        clave=clave,
        banda=int(m["banda"]),
        satelite=m["sat"],
        inicio=parsear_inicio(m["inicio"]),
        tam=tam,
    )


def listar_hora(bucket, producto, hora_utc, banda, sesion=None):
    """Archivos de una banda en una hora UTC dada."""
    sesion = sesion or requests.Session()
    prefijo = f"{producto}/{hora_utc:%Y}/{hora_utc:%j}/{hora_utc:%H}/"
    archivos = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefijo}
        if token:
            params["continuation-token"] = token
        r = sesion.get(f"https://{bucket}.s3.amazonaws.com/", params=params, timeout=30)
        r.raise_for_status()
        raiz = ET.fromstring(r.content)
        for obj in raiz.findall("s3:Contents", NS):
            a = parsear_clave(obj.findtext("s3:Key", namespaces=NS), int(obj.findtext("s3:Size", "0", NS)))
            if a and a.banda == banda:
                archivos.append(a)
        if raiz.findtext("s3:IsTruncated", namespaces=NS) == "true":
            token = raiz.findtext("s3:NextContinuationToken", namespaces=NS)
        else:
            break
    return sorted(archivos, key=lambda a: a.inicio)


def listar_rango(bucket, producto, banda, desde, hasta, sesion=None):
    """Todos los archivos de una banda entre dos instantes UTC."""
    sesion = sesion or requests.Session()
    hora = desde.replace(minute=0, second=0, microsecond=0)
    resultado = []
    while hora <= hasta:
        for a in listar_hora(bucket, producto, hora, banda, sesion):
            if desde <= a.inicio <= hasta:
                resultado.append(a)
        hora += timedelta(hours=1)
    return resultado


def tiempo_nominal(inicio, cada_min=10):
    """El escaneo de las 12:00 empieza 12:00:21: lo redondeamos hacia abajo
    al múltiplo de `cada_min`, que es el horario con el que se lo nombra."""
    minuto = (inicio.minute // cada_min) * cada_min
    return inicio.replace(minute=minuto, second=0, microsecond=0)
