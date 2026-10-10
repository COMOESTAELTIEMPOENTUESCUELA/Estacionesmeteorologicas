"""
Recortar una imagen ABI a nuestra región y guardarla como NetCDF chico.

Guardamos los valores ORIGINALES (enteros de 12 bits empaquetados con
scale_factor/add_offset, exactamente como los manda NOAA), más la bandera de
calidad DQF de cada píxel. No se reescala ni se interpola nada: para un estudio
riguroso, el archivo recortado tiene que ser bit a bit igual al original en esa
zona. Las imágenes PNG son solo para mirar; el dato es el NetCDF.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

import h5netcdf
import numpy as np
import xarray as xr

from .proyeccion import ParametrosGeo, ventana_de_recorte
from .remoto import ArchivoRemoto

# Nombre corto y longitud de onda central (µm) de las 16 bandas del ABI.
BANDAS = {
    1: ("Azul", 0.47), 2: ("Rojo (visible)", 0.64), 3: ("Vegetación", 0.86),
    4: ("Cirrus", 1.37), 5: ("Nieve/hielo", 1.6), 6: ("Tamaño de partícula", 2.2),
    7: ("IR onda corta", 3.9), 8: ("Vapor de agua alto", 6.2),
    9: ("Vapor de agua medio", 6.9), 10: ("Vapor de agua bajo", 7.3),
    11: ("Fase de nube", 8.4), 12: ("Ozono", 9.6), 13: ("IR limpio", 10.3),
    14: ("IR", 11.2), 15: ("IR sucio", 12.3), 16: ("CO2", 13.3),
}


@dataclass
class Recorte:
    datos: np.ndarray  # valores físicos (K o reflectancia 0-1), NaN = sin dato
    x: np.ndarray  # ángulos (rad)
    y: np.ndarray
    geo: ParametrosGeo
    banda: int
    inicio: datetime
    dataset: xr.Dataset  # listo para guardar a NetCDF


def _escalar(var, sl=()):
    """Aplica scale_factor/add_offset y _FillValue (h5netcdf entrega los
    enteros crudos, sin desempaquetar)."""
    crudo = var[sl] if sl else var[:]
    a = crudo.astype("float64")
    escala = float(var.attrs.get("scale_factor", 1.0))
    offset = float(var.attrs.get("add_offset", 0.0))
    a = a * escala + offset
    relleno = var.attrs.get("_FillValue")
    if relleno is not None:
        a[crudo == relleno] = np.nan
    return a, crudo


def _texto(valor):
    return valor.decode() if isinstance(valor, bytes) else str(valor)


def recortar(url, region, sesion=None, con_dqf=True):
    """Lee SOLO la región pedida de un archivo CMIP remoto."""
    remoto = ArchivoRemoto(url, sesion=sesion, tam_bloque=1024 * 1024)
    with h5netcdf.File(remoto, "r") as nc:
        proj = nc.variables["goes_imager_projection"]
        geo = ParametrosGeo.desde_atributos(proj.attrs)
        x, _ = _escalar(nc.variables["x"])
        y, _ = _escalar(nc.variables["y"])
        f0, f1, c0, c1 = ventana_de_recorte(x, y, region, geo)
        sl = (slice(f0, f1), slice(c0, c1))

        cmi = nc.variables["CMI"]
        datos, crudo = _escalar(cmi, sl)
        banda = int(np.asarray(nc.variables["band_id"][:]).ravel()[0])
        inicio = datetime.strptime(
            _texto(nc.attrs["time_coverage_start"])[:19], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)

        attrs_cmi = {
            k: _texto(cmi.attrs[k])
            for k in ("long_name", "standard_name", "units")
            if k in cmi.attrs
        }
        variables = {
            # Se pasan los valores físicos (float64) y el `encoding` de abajo los
            # vuelve a empaquetar con la misma escala: quedan los enteros originales.
            "CMI": (("y", "x"), datos, {**attrs_cmi, "grid_mapping": "goes_imager_projection"}),
        }
        if con_dqf and "DQF" in nc.variables:
            dqf = nc.variables["DQF"]
            variables["DQF"] = (("y", "x"), dqf[sl], {
                "long_name": "Bandera de calidad por píxel (0 = bueno)",
                "flag_values": np.asarray(dqf.attrs.get("flag_values", [])),
                "flag_meanings": _texto(dqf.attrs.get("flag_meanings", "")),
            })
        variables["goes_imager_projection"] = ((), np.int32(0), {
            k: (v if not isinstance(v, bytes) else v.decode())
            for k, v in proj.attrs.items()
        })

        ds = xr.Dataset(
            variables,
            coords={
                "x": ("x", x[c0:c1], {"units": "rad", "standard_name": "projection_x_coordinate", "axis": "X"}),
                "y": ("y", y[f0:f1], {"units": "rad", "standard_name": "projection_y_coordinate", "axis": "Y"}),
            },
            attrs={
                "title": f"GOES ABI banda {banda} recortada",
                "source": url,
                "time_coverage_start": inicio.isoformat(),
                "region_lon_lat": list(region),
                "ventana_filas_columnas": [f0, f1, c0, c1],
                "Conventions": "CF-1.7",
                "comment": "Valores CMI originales sin modificar (empaquetados). "
                           "Abrir con xarray: se desempaquetan solos.",
            },
        )
        # El CMI se guarda con el mismo empaquetado que el original:
        # al abrirlo con xarray se convierte solo a K / reflectancia.
        ds["CMI"].encoding = {
            "dtype": "int16",
            "scale_factor": float(cmi.attrs["scale_factor"]),
            "add_offset": float(cmi.attrs["add_offset"]),
            "_FillValue": int(cmi.attrs["_FillValue"]),
        }
        ds["CMI"].attrs.pop("_FillValue", None)

    rec = Recorte(datos=datos, x=x[c0:c1], y=y[f0:f1], geo=geo, banda=banda, inicio=inicio, dataset=ds)
    rec.bytes_bajados = remoto.bytes_bajados
    return rec


def guardar_netcdf(rec: Recorte, ruta):
    """Guarda el recorte comprimido (zlib). ~0,3-1 MB por imagen."""
    encoding = {v: {"zlib": True, "complevel": 4} for v in rec.dataset.data_vars if v != "goes_imager_projection"}
    for v, enc in encoding.items():
        enc.update(rec.dataset[v].encoding)
    rec.dataset.to_netcdf(ruta, engine="h5netcdf", encoding=encoding)


def abrir_recorte(ruta_nc):
    """Vuelve a armar un Recorte desde un NetCDF guardado (para redibujar
    imágenes sin bajar nada de nuevo, por ejemplo si se cambia una paleta)."""
    ds = xr.open_dataset(ruta_nc, engine="h5netcdf")
    geo = ParametrosGeo.desde_atributos(ds["goes_imager_projection"].attrs)
    banda = int(str(ds.attrs.get("title", "")).split("banda ")[1].split()[0])
    inicio = datetime.fromisoformat(ds.attrs["time_coverage_start"])
    datos = ds["CMI"].values.astype("float64")
    return Recorte(datos=datos, x=ds["x"].values, y=ds["y"].values, geo=geo,
                   banda=banda, inicio=inicio, dataset=ds)
