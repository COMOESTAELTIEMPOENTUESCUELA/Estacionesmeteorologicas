"""
Notificaciones ("avisos") de la red del grupo: la 🔔 de la página y, en los
celulares que lo permitan, un aviso del sistema aunque la página esté cerrada
(Web Push).

Qué avisa (a la persona afectada, nunca a quien lo hizo):
- comentaron o reaccionaron a una nota tuya o a un pronóstico tuyo;
- respondieron un comentario tuyo;
- un mensaje nuevo en una conversación tuya;
- un coordinador trató (y respondió) tu pregunta en la reunión.

Los avisos parecidos se JUNTAN mientras no los leíste: «Ana, Beto y 1 más
reaccionaron a tu nota» es una sola fila (clave = qué cosa), no tres.

Web Push, en corto: el navegador del celular le pide a SU servicio de avisos
(Google, Apple o Mozilla) una dirección única ("endpoint") y nos la da; para
avisar, el servidor le manda a esa dirección el mensaje cifrado con las claves
del navegador y firmado con nuestra clave VAPID (así el servicio sabe que es
siempre el mismo servidor). Solo anda en páginas seguras (https: la copia de
Tailscale Funnel; no en http://IP:8080). En iPhone, solo con la página
instalada en la pantalla de inicio (iOS 16.4 o más).
"""
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb

from . import cuentas as c

log = logging.getLogger(__name__)
router = APIRouter()

# Solo se aceptan direcciones de los servicios de avisos de verdad: si no,
# alguien podría registrar una dirección cualquiera y hacer que el servidor le
# mande pedidos a una máquina interna (eso se llama SSRF).
SERVICIOS_PUSH = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
                  "push.services.mozilla.com", "web.push.apple.com", "notify.windows.com")
MAX_SUSCRIPCIONES = 10            # dispositivos por persona
_envios = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")


def _servicio_valido(endpoint):
    u = urlparse(endpoint or "")
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == s or host.endswith("." + s) for s in SERVICIOS_PUSH)


# ---------------------------------------------------------------------------
# Claves VAPID (se crean solas la primera vez y quedan en la base)
# ---------------------------------------------------------------------------
_clave = {}


def _claves():
    if _clave:
        return _clave
    filas = c._leer("SELECT privada, publica FROM push_clave WHERE id = 1")
    if not filas:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        privada = v.private_pem().decode()
        publica = base64.urlsafe_b64encode(v.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip("=")
        # Si dos procesos la crean a la vez, gana la primera y los dos usan esa.
        c._escribir("INSERT INTO push_clave (id, privada, publica) VALUES (1, %s, %s) ON CONFLICT DO NOTHING", (privada, publica))
        filas = c._leer("SELECT privada, publica FROM push_clave WHERE id = 1")
    _clave.update(filas[0])
    return _clave


def clave_publica():
    try:
        return _claves()["publica"]
    except Exception as ex:          # sin la librería o sin la tabla: la 🔔 anda igual, sin celular
        log.warning("avisos al celular no disponibles: %s", ex)
        return None


# ---------------------------------------------------------------------------
# Crear un aviso
# ---------------------------------------------------------------------------
def notificar(para, tipo, clave, enlace, actor_id, extracto=""):
    """Un aviso para `para` (id de usuario). Si ya tiene uno SIN LEER con la
    misma clave, se le suma el actor y sube arriba. Nunca falla hacia afuera:
    un problema con los avisos no puede impedir comentar o mandar un mensaje."""
    if not para or para == actor_id:
        return None
    try:
        filas = c._escribir("""
            INSERT INTO notificacion (usuario_id, tipo, clave, enlace, actores, extracto)
            VALUES (%s, %s, %s, %s, ARRAY[%s]::int[], %s)
            ON CONFLICT (usuario_id, clave) WHERE NOT leida DO UPDATE SET
                actores = array_append(array_remove(notificacion.actores, %s), %s),
                extracto = EXCLUDED.extracto, tipo = EXCLUDED.tipo, creada = now()
            RETURNING id""", (para, tipo, clave, Jsonb(enlace), actor_id, (extracto or "")[:200], actor_id, actor_id))
        aviso = _armar(c._leer("SELECT * FROM notificacion WHERE id = %s", (filas[0]["id"],)))[0]
        _envios.submit(_mandar_push, para, aviso)
        return aviso["id"]
    except Exception:
        log.exception("no se pudo crear el aviso")
        return None


def dueño_de(objeto):
    """Quién es el autor de una nota ("nota:5") o de un pronóstico ("pron|ciudad|fecha|firma")."""
    if objeto.startswith("nota:") and objeto[5:].isdigit():
        f = c._leer("SELECT usuario_id FROM nota WHERE id = %s", (int(objeto[5:]),))
        return f[0]["usuario_id"] if f else None
    partes = objeto.split("|")
    if len(partes) == 4 and partes[0] == "pron":
        f = c._leer("SELECT id FROM usuario WHERE activo AND %s = ANY(firmas)", (partes[3],))
        return f[0]["id"] if f else None
    return None


# ---------------------------------------------------------------------------
# Texto de cada aviso
# ---------------------------------------------------------------------------
def _quienes(nombres):
    nombres = [n.split(" ")[0] for n in nombres]
    if len(nombres) <= 2:
        return " y ".join(nombres) or "Alguien"
    return f"{nombres[0]}, {nombres[1]} y {len(nombres) - 2} más"


def _que(enlace):
    obj = enlace.get("objeto", "")
    if obj.startswith("pron|"):
        _, ciudad, fecha, _ = obj.split("|")
        return f"tu pronóstico de {ciudad} del {fecha[8:10]}/{fecha[5:7]}"
    return "tu nota"


def _texto(n, nombres):
    quien = _quienes(nombres)
    varios = len(nombres) > 1
    ext = f": «{n['extracto']}»" if n["extracto"] else ""
    if n["tipo"] == "comentario":
        return f"{quien} {'comentaron' if varios else 'comentó'} {_que(n['enlace'])}{ext}"
    if n["tipo"] == "respuesta":
        return f"{quien} {'respondieron' if varios else 'respondió'} tu comentario{ext}"
    if n["tipo"] == "reaccion":
        return f"{quien} {'reaccionaron' if varios else 'reaccionó'} a {_que(n['enlace'])}"
    if n["tipo"] == "mensaje":
        donde = n["enlace"].get("nombre")
        return f"{quien}{' en ' + donde if donde else ''}{ext or ': te mandó un mensaje'}"
    if n["tipo"] == "reunion":
        return f"{quien} trató tu pregunta en la reunión{ext}"
    return quien


def _armar(filas):
    ids = {a for f in filas for a in f["actores"]}
    nombres = {u["id"]: u["nombre"] for u in c._leer("SELECT id, nombre FROM usuario WHERE id = ANY(%s)", (list(ids),))} if ids else {}
    salida = []
    for f in filas:
        quienes = [nombres.get(a, "Alguien") for a in reversed(f["actores"])]       # el último, primero
        salida.append({"id": f["id"], "tipo": f["tipo"], "texto": _texto(f, quienes), "enlace": f["enlace"],
                       "creada": f["creada"], "leida": f["leida"], "actor_id": f["actores"][-1] if f["actores"] else None})
    return salida


# ---------------------------------------------------------------------------
# Mandar al celular
# ---------------------------------------------------------------------------
def _mandar_push(usuario_id, aviso):
    subs = c._leer("SELECT id, endpoint, p256dh, auth FROM push_suscripcion WHERE usuario_id = %s", (usuario_id,))
    if not subs:
        return
    try:
        from py_vapid import Vapid
        from pywebpush import WebPushException, webpush
        vapid = Vapid.from_pem(_claves()["privada"].encode())
    except Exception as ex:
        log.warning("avisos al celular no disponibles: %s", ex)
        return
    no_leidas = c._leer("SELECT count(*) AS n FROM notificacion WHERE usuario_id = %s AND NOT leida", (usuario_id,))[0]["n"]
    datos = json.dumps({"titulo": "Meteo UNLP", "texto": aviso["texto"], "url": f"/#aviso-{aviso['id']}",
                        "etiqueta": f"aviso-{aviso['id']}", "no_leidas": no_leidas}, ensure_ascii=False)
    # "sub" identifica al servidor ante el servicio de avisos (un https o un mailto).
    sub = c.URL_PUBLICA if c.URL_PUBLICA.startswith("https://") else "mailto:avisos@meteo.invalid"
    for s in subs:
        try:
            webpush({"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}, datos,
                    vapid_private_key=vapid, vapid_claims={"sub": sub}, ttl=24 * 3600, timeout=15)
        except WebPushException as ex:
            codigo = getattr(ex.response, "status_code", None)
            if codigo in (404, 410):          # el celular la dio de baja: se borra
                c._escribir("DELETE FROM push_suscripcion WHERE id = %s", (s["id"],))
            else:
                log.warning("aviso al celular falló (%s): %s", codigo, ex)
        except Exception as ex:
            log.warning("aviso al celular falló: %s", ex)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@router.get("/api/yo/avisos")
def avisos(request: Request):
    """Los últimos 50 avisos (los sin leer primero en el contador)."""
    u = c.requiere_usuario(request)
    filas = c._leer("SELECT * FROM notificacion WHERE usuario_id = %s ORDER BY creada DESC LIMIT 50", (u["id"],))
    no_leidas = c._leer("SELECT count(*) AS n FROM notificacion WHERE usuario_id = %s AND NOT leida", (u["id"],))[0]["n"]
    return {"no_leidas": no_leidas, "items": _armar(filas), "clave_push": clave_publica()}


@router.post("/api/yo/avisos/leidas")
async def marcar_leidas(request: Request):
    """{"ids": [..]} marca esos; sin ids, todos."""
    u = c.requiere_usuario(request)
    datos = await request.json()
    ids = [int(i) for i in (datos.get("ids") or []) if str(i).isdigit()]
    if ids:
        c._escribir("UPDATE notificacion SET leida = true WHERE usuario_id = %s AND id = ANY(%s)", (u["id"], ids))
    else:
        c._escribir("UPDATE notificacion SET leida = true WHERE usuario_id = %s AND NOT leida", (u["id"],))
    return {"ok": True}


def leer_chat(usuario_id, cid):
    """Al abrir una conversación, su aviso queda leído."""
    c._escribir("UPDATE notificacion SET leida = true WHERE usuario_id = %s AND clave = %s AND NOT leida",
                (usuario_id, f"chat:{cid}"))


@router.post("/api/yo/avisos/celular")
async def suscribir(request: Request):
    """Este celular/navegador quiere avisos: guarda su dirección y sus claves."""
    u = c.requiere_usuario(request)
    datos = await request.json()
    endpoint = str(datos.get("endpoint") or "")[:1000]
    keys = datos.get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or "")[:200], str(keys.get("auth") or "")[:100]
    if not _servicio_valido(endpoint) or not p256dh or not auth:
        raise HTTPException(400, "Suscripción no válida")
    c._escribir("""INSERT INTO push_suscripcion (usuario_id, endpoint, p256dh, auth, agente) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (endpoint) DO UPDATE SET usuario_id = EXCLUDED.usuario_id, p256dh = EXCLUDED.p256dh,
                                                        auth = EXCLUDED.auth, agente = EXCLUDED.agente""",
                (u["id"], endpoint, p256dh, auth, (request.headers.get("user-agent") or "")[:300]))
    # Como mucho MAX_SUSCRIPCIONES por persona: se van las más viejas.
    c._escribir("""DELETE FROM push_suscripcion WHERE usuario_id = %s AND id NOT IN (
                       SELECT id FROM push_suscripcion WHERE usuario_id = %s ORDER BY creada DESC LIMIT %s)""",
                (u["id"], u["id"], MAX_SUSCRIPCIONES))
    return {"ok": True}


@router.delete("/api/yo/avisos/celular")
async def desuscribir(request: Request):
    u = c.requiere_usuario(request)
    datos = await request.json()
    c._escribir("DELETE FROM push_suscripcion WHERE usuario_id = %s AND endpoint = %s", (u["id"], str(datos.get("endpoint") or "")))
    return {"ok": True}


@router.post("/api/yo/avisos/probar")
def probar(request: Request):
    """Un aviso de prueba al propio celular (para ver si llegan)."""
    u = c.requiere_usuario(request)
    if not c._leer("SELECT 1 FROM push_suscripcion WHERE usuario_id = %s", (u["id"],)):
        raise HTTPException(400, "Este usuario no tiene ningún celular con avisos activados")
    _envios.submit(_mandar_push, u["id"], {"id": 0, "texto": "🔔 Prueba: los avisos llegan a este celular."})
    return {"ok": True}
