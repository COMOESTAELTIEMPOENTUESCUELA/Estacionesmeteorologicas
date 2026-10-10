"""
Leer un archivo NetCDF remoto SIN bajarlo entero.

Un archivo ABI de disco completo pesa ~24 MB (banda 13) y hasta ~400 MB (banda 2,
visible a 500 m). Pero adentro, los datos están guardados en "chunks" (bloques)
comprimidos por separado: el formato NetCDF-4/HDF5 tiene un índice que dice en
qué byte empieza cada bloque. Entonces, si solo queremos el pedacito que cubre
Buenos Aires, alcanza con:

  1) leer el índice (unos pocos KB al principio del archivo),
  2) pedir únicamente los bloques que se superponen con nuestra región.

Los servidores de Amazon S3 aceptan el header HTTP `Range: bytes=inicio-fin`,
que devuelve solo ese tramo del archivo. Esta clase envuelve eso en un objeto
"tipo archivo" (con read/seek) que la librería h5py sabe usar como si fuera un
archivo del disco. Para no hacer miles de pedidos chiquitos, se lee de a
bloques de `tam_bloque` bytes y se guardan en memoria.
"""
import io

import requests


class ArchivoRemoto(io.RawIOBase):
    def __init__(self, url, sesion=None, tam_bloque=256 * 1024, timeout=60):
        super().__init__()
        self.url = url
        self.sesion = sesion or requests.Session()
        self.tam_bloque = tam_bloque
        self.timeout = timeout
        self.pos = 0
        self.cache = {}  # número de bloque -> bytes
        self.bytes_bajados = 0
        self.pedidos = 0
        r = self.sesion.head(url, timeout=timeout)
        r.raise_for_status()
        self.tam = int(r.headers["Content-Length"])

    # --- interfaz de archivo que usa h5py ---
    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        elif whence == io.SEEK_END:
            self.pos = self.tam + offset
        return self.pos

    def readinto(self, buffer):
        n = min(len(buffer), self.tam - self.pos)
        if n <= 0:
            return 0
        datos = self._leer(self.pos, n)
        buffer[:n] = datos
        self.pos += n
        return n

    # --- lo interesante ---
    def _leer(self, inicio, n):
        primero = inicio // self.tam_bloque
        ultimo = (inicio + n - 1) // self.tam_bloque
        faltan = [b for b in range(primero, ultimo + 1) if b not in self.cache]
        if faltan:
            # Bloques consecutivos que faltan se piden en un solo Range.
            self._bajar_bloques(faltan[0], faltan[-1])
        trozo = b"".join(self.cache[b] for b in range(primero, ultimo + 1))
        desde = inicio - primero * self.tam_bloque
        return trozo[desde:desde + n]

    def _bajar_bloques(self, b0, b1):
        ini = b0 * self.tam_bloque
        fin = min((b1 + 1) * self.tam_bloque, self.tam) - 1
        r = self.sesion.get(self.url, headers={"Range": f"bytes={ini}-{fin}"}, timeout=self.timeout)
        r.raise_for_status()
        if r.status_code != 206:
            raise IOError(f"El servidor no respetó el Range (HTTP {r.status_code})")
        contenido = r.content
        self.pedidos += 1
        self.bytes_bajados += len(contenido)
        for i, b in enumerate(range(b0, b1 + 1)):
            self.cache[b] = contenido[i * self.tam_bloque:(i + 1) * self.tam_bloque]
