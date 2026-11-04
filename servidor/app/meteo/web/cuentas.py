"""
Cuentas de los pronosticadores: ingreso con Google, perfil, "Mis pronósticos"
y la libreta de notas (con el tablero de la reunión sinóptica).

La página pública de Cartas sigue igual para todos; quien INGRESA ve además
su perfil y puede anotar. Ver docs/07-cuentas.md.

Seguridad, paso por paso:
- Google dice QUIÉN es (con su mail verificado); la tabla `usuario` dice si
  puede entrar: solo los mails cargados por un administrador (lista de
  permitidos). No se guarda ninguna contraseña.
- El ingreso usa el flujo "código de autorización" de OAuth con `state` (que
  la respuesta venga del pedido que hicimos nosotros) y PKCE (que el código,
  si alguien lo intercepta, no sirva sin el secreto que quedó acá). El
  canje del código y la consulta del mail se hacen servidor a servidor con
  Google, por HTTPS.
- La sesión es un token aleatorio en una cookie HttpOnly (el JavaScript de la
  página no la puede leer), Secure (solo por HTTPS) y SameSite=Lax (otro
  sitio no la puede usar para mandar pedidos en nombre del usuario). En la
  base se guarda solo su huella (sha256). Vence a los 30 días.
- Todo lo que modifica algo (/api/yo con POST, PUT, DELETE) exige además el
  encabezado "X-Pedido": un formulario de otro sitio no lo puede agregar sin
  permiso del navegador (CORS), así que no puede falsificar pedidos.
- Permisos: las notas privadas las ve quien las escribió y los
  administradores (la página lo avisa); las compartidas, todos los que
  ingresaron. Las métricas de cada uno, él y los coordinadores.

Configuración (.env): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y URL_PUBLICA
(la dirección https con que se entra, ej. https://xxx.ts.net). Sin eso, el
botón de ingresar no aparece y todo sigue como antes.
"""
import argparse
import base64
import hashlib
import logging
import os
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)
router = APIRouter()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
URL_PUBLICA = os.environ.get("URL_PUBLICA", "").rstrip("/")
GOOGLE_AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USUARIO = "https://openidconnect.googleapis.com/v1/userinfo"

COOKIE = "meteo_sesion"
COOKIE_OAUTH = "meteo_oauth"
DURACION = timedelta(days=30)
TIPOS_NOTA = ("vi", "llamo_atencion", "duda", "pregunta")
# Del contexto de una nota solo se guardan estas claves (texto o número corto).
CLAVES_CONTEXTO = ("ts", "producto", "corrida", "plazo", "carta", "vista", "ciudad", "desde", "hasta", "seccion")
# La imagen pegada a una nota ("pizarra"): lo que manda la página (data URL
# PNG/JPEG/WebP) se abre con Pillow y se vuelve a codificar en WebP, así se
# guarda siempre una imagen limpia y chica, sin importar lo que llegó.
MAX_IMAGEN_ENTRADA = 4_000_000        # bytes de la data URL
MAX_LADO = 1600                       # px: más grande se achica
SQL = Path(__file__).parent / "cuentas.sql"

_pools = {}


def configurar(pool_lectura, pool_escritura):
    """La app le pasa sus conexiones a la base (las de lectura y escritura)."""
    _pools["l"], _pools["e"] = pool_lectura, pool_escritura


def login_disponible():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and URL_PUBLICA)


def _leer(sql, params=()):
    with _pools["l"].connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _escribir(sql, params=()):
    with _pools["e"].connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        filas = cur.fetchall() if cur.description else []
        conn.commit()
        return filas


def _huella(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _seguro():
    return URL_PUBLICA.startswith("https://")


# ---------------------------------------------------------------------------
# Quién es el que pide
# ---------------------------------------------------------------------------
def usuario_actual(request: Request):
    """El usuario con sesión válida, o None (y se recuerda en el pedido)."""
    ya = getattr(request.state, "usuario", False)
    if ya is not False:
        return ya
    u = None
    token = request.cookies.get(COOKIE)
    if token and _pools:
        filas = _leer("""
            SELECT u.id, u.email, u.nombre, u.rol, u.firmas, u.bio, u.metricas_publicas
            FROM sesion s JOIN usuario u ON u.id = s.usuario_id
            WHERE s.huella = %s AND s.expira > now() AND u.activo""", (_huella(token),))
        u = filas[0] if filas else None
    request.state.usuario = u
    return u


def requiere_usuario(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(401, "Hay que ingresar")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-pedido") != "libreta":
        raise HTTPException(403, "Pedido no válido")      # protección contra pedidos falsificados (CSRF)
    return u


def es_coordinador(u):
    return u["rol"] in ("coordinador", "admin")


def puede_ver_metricas(u, de):
    """Las métricas de alguien las ve esa persona, los coordinadores y, si
    eligió mostrarlas, todo el grupo."""
    return de.get("id") == u["id"] or es_coordinador(u) or bool(de.get("metricas_publicas"))


# ---------------------------------------------------------------------------
# Ingreso con Google
# ---------------------------------------------------------------------------
def _pagina(titulo, texto, codigo=200):
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titulo}</title><body style="font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h1 style="font-size:22px">{titulo}</h1><p>{texto}</p><p><a href="/">Volver a las cartas</a></p></body>""", status_code=codigo)


@router.get("/auth/google")
def ingresar():
    if not login_disponible():
        return _pagina("Ingreso no configurado", "Falta configurar el ingreso con Google en el servidor.", 503)
    state = secrets.token_urlsafe(24)
    verificador = secrets.token_urlsafe(48)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    url = GOOGLE_AUTORIZAR + "?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": URL_PUBLICA + "/auth/google/volver",
        "response_type": "code", "scope": "openid email profile", "state": state,
        "code_challenge": desafio, "code_challenge_method": "S256", "prompt": "select_account"})
    r = RedirectResponse(url, status_code=302)
    # state y verificador quedan en una cookie corta, solo para /auth.
    r.set_cookie(COOKIE_OAUTH, f"{state}.{verificador}", max_age=600, httponly=True, secure=_seguro(),
                 samesite="lax", path="/auth")
    return r


@router.get("/auth/google/volver")
def volver(request: Request, code: str = "", state: str = "", error: str = ""):
    guardado = request.cookies.get(COOKIE_OAUTH, "")
    st, _, verificador = guardado.partition(".")
    if error or not code or not st or not secrets.compare_digest(st, state):
        return _pagina("No se pudo ingresar", "El ingreso se canceló o venció. Probá de nuevo.", 400)
    try:
        t = requests.post(GOOGLE_TOKEN, data={
            "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "code": code,
            "code_verifier": verificador, "grant_type": "authorization_code",
            "redirect_uri": URL_PUBLICA + "/auth/google/volver"}, timeout=20)
        t.raise_for_status()
        info = requests.get(GOOGLE_USUARIO, headers={"Authorization": f"Bearer {t.json()['access_token']}"}, timeout=20)
        info.raise_for_status()
        info = info.json()
    except Exception as ex:
        log.warning("ingreso con Google: %s", ex)
        return _pagina("No se pudo ingresar", "Google no respondió. Probá de nuevo en un rato.", 502)
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"):
        return _pagina("No se pudo ingresar", "Google no confirmó el mail de esa cuenta.", 403)
    filas = _leer("SELECT id FROM usuario WHERE email = %s AND activo", (email,))
    if not filas:
        log.info("ingreso rechazado: %s", email)
        return _pagina("Cuenta no habilitada",
                       f"La cuenta <b>{email}</b> no está en la lista del grupo. Pedile a un coordinador que la agregue.", 403)
    token = secrets.token_urlsafe(32)
    _escribir("INSERT INTO sesion (huella, usuario_id, expira, ip, agente) VALUES (%s, %s, %s, %s, %s)",
              (_huella(token), filas[0]["id"], datetime.now(timezone.utc) + DURACION,
               request.headers.get("x-forwarded-for", request.client.host if request.client else None),
               (request.headers.get("user-agent") or "")[:200]))
    _escribir("UPDATE usuario SET ultimo_ingreso = now() WHERE id = %s", (filas[0]["id"],))
    _escribir("DELETE FROM sesion WHERE expira < now()")
    log.info("ingresó %s", email)
    r = RedirectResponse("/#perfil", status_code=302)
    r.set_cookie(COOKIE, token, max_age=int(DURACION.total_seconds()), httponly=True, secure=_seguro(), samesite="lax")
    r.delete_cookie(COOKIE_OAUTH, path="/auth")
    return r


@router.post("/auth/salir")
def salir(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        _escribir("DELETE FROM sesion WHERE huella = %s", (_huella(token),))
    r = Response(status_code=204)
    r.delete_cookie(COOKIE)
    return r


# ---------------------------------------------------------------------------
# Perfil
# ---------------------------------------------------------------------------
@router.get("/api/yo")
def yo(request: Request):
    u = usuario_actual(request)
    return {"login": login_disponible(), "url_publica": URL_PUBLICA, "usuario": u}


def _rango(desde, hasta):
    hasta = hasta or datetime.now(timezone(timedelta(hours=-3))).date() - timedelta(days=1)
    desde = desde or hasta - timedelta(days=90)
    if desde > hasta or (hasta - desde).days > 3 * 366:
        raise HTTPException(400, "Rango de fechas inválido (máximo 3 años)")
    return desde, hasta


def _vigentes(desde, hasta):
    """La versión VIGENTE de cada pronóstico del período (de quien sea)."""
    from ..verificacion import puntajes
    dias = _leer("""
        SELECT e.ciudad, e.fecha_pronostico, e.capturado, e.publicado_por, e.es_correccion,
               COALESCE((e.contenido -> 'publicado_el' ->> 0)::date,
                        (e.capturado AT TIME ZONE 'America/Argentina/Buenos_Aires')::date) AS publicado_el,
               d.fecha, d.plazo_dias, d.temp_min, d.temp_max, d.temp_min_suburbana, d.temp_max_suburbana, d.periodos
        FROM pronostico_dia d JOIN pronostico_emision e ON e.id = d.emision_id
        WHERE d.fecha BETWEEN %s AND %s""", (desde, hasta))
    return puntajes.elegir_emisiones(dias, "final")


def _pares(vigentes, desde, hasta):
    """Pronóstico contra lo observado, día por día."""
    from ..verificacion import puntajes
    ciudades = sorted({d["ciudad"] for d in vigentes})
    observado = {(r["ciudad"], r["fecha"], r["elemento"]): r for r in _leer("""
        SELECT ciudad, fecha, elemento, valor, categoria, estacion_id, detalle FROM verif_observado
        WHERE ciudad = ANY(%s) AND fecha BETWEEN %s AND %s""", (ciudades, desde - timedelta(days=5), hasta))}
    filas = puntajes.armar_pares(vigentes, observado)
    filas.sort(key=lambda f: (f["fecha"], f["ciudad"], f["plazo"]))
    return filas


@router.get("/api/yo/pronosticos")
def mis_pronosticos(request: Request, desde: date | None = None, hasta: date | None = None,
                    usuario_id: int | None = None, firma: str = ""):
    """Los pronósticos del usuario (por su firma) contra lo observado, y sus
    medidas de error por plazo (las mismas de la pestaña Verificación).
    Un coordinador puede pedir los de otro (usuario_id) o los de una firma
    (aunque esa persona no tenga usuario)."""
    from ..verificacion import puntajes
    u = requiere_usuario(request)
    quien = u
    if firma:
        if not es_coordinador(u):
            raise HTTPException(403, "Solo los coordinadores ven las métricas de una firma sin usuario")
        quien = {"id": None, "nombre": firma, "firmas": [firma]}
    elif usuario_id and usuario_id != u["id"]:
        filas = _leer("SELECT id, email, nombre, rol, firmas, metricas_publicas FROM usuario WHERE id = %s", (usuario_id,))
        if not filas:
            raise HTTPException(404, "usuario desconocido")
        quien = filas[0]
        if not puede_ver_metricas(u, quien):
            raise HTTPException(403, "Sus métricas son privadas (las ven esa persona y los coordinadores)")
    desde, hasta = _rango(desde, hasta)
    salida = {"usuario": {k: quien[k] for k in ("id", "nombre", "firmas")}, "desde": desde, "hasta": hasta,
              "resumen": {}, "dias": []}
    if not quien["firmas"]:
        return salida
    # Primero la versión vigente de cada pronóstico (de quien sea) y DESPUÉS
    # las de esta persona: si otro corrigió su pronóstico, cuenta la corrección.
    filas = _pares([d for d in _vigentes(desde, hasta) if d["publicado_por"] in quien["firmas"]], desde, hasta)
    salida["resumen"] = puntajes.resumen(filas)
    salida["dias"] = filas
    return salida


@router.get("/api/yo/pronosticos/grupo")
def pronosticos_del_grupo(request: Request, desde: date | None = None, hasta: date | None = None):
    """Para los coordinadores: el resumen de CADA firma del período (aunque no
    tenga usuario), con el mismo criterio que Mis pronósticos."""
    from ..verificacion import puntajes
    u = requiere_usuario(request)
    if not es_coordinador(u):
        raise HTTPException(403, "Solo coordinadores")
    desde, hasta = _rango(desde, hasta)
    vigentes = _vigentes(desde, hasta)
    todas = _pares(vigentes, desde, hasta)
    usuarios = {f: x for x in _leer("SELECT id, nombre, firmas FROM usuario WHERE activo") for f in x["firmas"]}
    salida = []
    for firma in sorted({f["pronosticador"] for f in todas if f["pronosticador"]}):
        filas = [f for f in todas if f["pronosticador"] == firma]
        us = usuarios.get(firma)
        salida.append({"firma": firma, "usuario": {"id": us["id"], "nombre": us["nombre"]} if us else None,
                       "dias": len(filas), "emisiones": len({(f["ciudad"], f["fecha_emision"]) for f in filas}),
                       "resumen": puntajes.resumen(filas)})
    return {"desde": desde, "hasta": hasta, "pronosticadores": salida}


@router.get("/api/yo/usuarios")
def usuarios(request: Request):
    """Para los coordinadores: quiénes están (para ver sus métricas)."""
    u = requiere_usuario(request)
    if not es_coordinador(u):
        raise HTTPException(403, "Solo coordinadores")
    return _leer("SELECT id, nombre, rol, firmas, ultimo_ingreso FROM usuario WHERE activo ORDER BY nombre")


# ---------------------------------------------------------------------------
# Libreta
# ---------------------------------------------------------------------------
def _limpiar_nota(datos):
    tipo = datos.get("tipo", "vi")
    if tipo not in TIPOS_NOTA:
        raise HTTPException(400, f"tipo: uno de {', '.join(TIPOS_NOTA)}")
    texto = str(datos.get("texto") or "").strip()
    if not 1 <= len(texto) <= 10000:
        raise HTTPException(400, "El texto tiene que tener entre 1 y 10.000 caracteres")
    etiquetas = [str(e).strip().lower()[:30] for e in (datos.get("etiquetas") or []) if str(e).strip()][:10]
    contexto = {k: v for k, v in (datos.get("contexto") or {}).items()
                if k in CLAVES_CONTEXTO and isinstance(v, (str, int, float)) and len(str(v)) <= 60}
    fecha = datos.get("fecha_evento")
    try:
        fecha = date.fromisoformat(fecha) if fecha else None
    except (TypeError, ValueError):
        raise HTTPException(400, "fecha_evento: AAAA-MM-DD")
    return tipo, texto, etiquetas, bool(datos.get("compartida")), contexto, fecha


def _limpiar_imagen(dato):
    """data URL -> (bytes WebP, ancho, alto). Rechaza lo que no sea imagen."""
    import binascii
    import io

    from PIL import Image, UnidentifiedImageError
    if not isinstance(dato, str) or len(dato) > MAX_IMAGEN_ENTRADA:
        raise HTTPException(400, "imagen: muy grande (hasta ~3 MB)")
    cabeza, _, cuerpo = dato.partition(",")
    if cabeza not in ("data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"):
        raise HTTPException(400, "imagen: PNG, JPEG o WebP en base64")
    try:
        img = Image.open(io.BytesIO(base64.b64decode(cuerpo, validate=True)))
        if img.width * img.height > 40_000_000:
            raise HTTPException(400, "imagen: demasiados píxeles")
        img.load()
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "imagen: no se pudo leer")
    img = img.convert("RGB")
    img.thumbnail((MAX_LADO, MAX_LADO * 3))
    salida = io.BytesIO()
    img.save(salida, "WEBP", quality=82, method=4)
    return salida.getvalue(), img.width, img.height


def _preparar_imagen(datos):
    """Con "imagen" en el pedido: una data URL la reemplaza, null la saca.
    Sin esa clave, la imagen queda como estaba (devuelve None). Con "capa",
    el dibujo encima (elementos, como los dibujos de las cartas)."""
    if "imagen" not in datos:
        return None
    if datos["imagen"] is None:
        return "sacar"
    capa = None
    if datos.get("capa"):
        import json
        from .app import _validar_dibujo
        if len(json.dumps(datos["capa"])) > 400_000:
            raise HTTPException(400, "capa: el dibujo es demasiado grande")
        capa = _validar_dibujo(datos["capa"])
    return (*_limpiar_imagen(datos["imagen"]), capa)


def _guardar_imagen(nota_id, imagen):
    if imagen is None:
        return
    if imagen == "sacar":
        _escribir("DELETE FROM nota_imagen WHERE nota_id = %s", (nota_id,))
        return
    webp, ancho, alto, capa = imagen
    _escribir("""INSERT INTO nota_imagen (nota_id, datos, ancho, alto, capa) VALUES (%s, %s, %s, %s, %s)
                 ON CONFLICT (nota_id) DO UPDATE SET datos = EXCLUDED.datos, ancho = EXCLUDED.ancho,
                 alto = EXCLUDED.alto, capa = EXCLUDED.capa, creada = now()""",
              (nota_id, webp, ancho, alto, Jsonb(capa) if capa else None))


@router.get("/api/yo/notas")
def notas(request: Request, desde: date | None = None, hasta: date | None = None, tipo: str = "",
          mias: bool = False, pendientes: bool = False, etiqueta: str = "", autor_id: int | None = None,
          todas: bool = False):
    """Las notas propias y las compartidas por los demás (con su autor).
    Un ADMINISTRADOR, con todas=true, ve también las privadas de todos (la
    página avisa a cada uno que las privadas las ven los administradores)."""
    u = requiere_usuario(request)
    if todas and u["rol"] != "admin":
        raise HTTPException(403, "Solo los administradores ven las notas privadas de otros")
    cond, params = (["true"], []) if todas else (["(n.usuario_id = %s OR n.compartida)"], [u["id"]])
    if mias:
        cond.append("n.usuario_id = %s"); params.append(u["id"])
    if autor_id:
        cond.append("n.usuario_id = %s"); params.append(autor_id)
    if desde:
        cond.append("n.fecha_evento >= %s"); params.append(desde)
    if hasta:
        cond.append("n.fecha_evento <= %s"); params.append(hasta)
    if tipo:
        cond.append("n.tipo = %s"); params.append(tipo)
    if pendientes:
        cond.append("NOT n.tratada")
    if etiqueta:
        cond.append("%s = ANY(n.etiquetas)"); params.append(etiqueta.lower())
    return _notas_con_extras(u, cond, params)


def _notas_con_extras(u, cond, params, limite=500):
    """Las notas que cumplen `cond`, con su autor, imagen, comentarios y reacciones."""
    filas = _leer(f"""
        SELECT n.id, n.usuario_id, us.nombre AS autor, n.creada, n.actualizada, n.tipo, n.texto, n.etiquetas,
               n.compartida, n.fecha_evento, n.contexto, n.tratada, n.respuesta, tr.nombre AS tratada_por,
               ni.creada AS captura_el, ni.capa
        FROM nota n JOIN usuario us ON us.id = n.usuario_id LEFT JOIN usuario tr ON tr.id = n.tratada_por
        LEFT JOIN nota_imagen ni ON ni.nota_id = n.id
        WHERE {' AND '.join(cond)} ORDER BY COALESCE(n.fecha_evento, n.creada::date) DESC, n.creada DESC LIMIT {int(limite)}""",
                  tuple(params))
    for f in filas:
        f["autor_id"] = f.pop("usuario_id")
        f["mia"] = f["autor_id"] == u["id"]
        f["imagen"] = _miniatura(f["contexto"] or {})
        cap = f.pop("captura_el")
        f["captura"] = f"/api/yo/notas/{f['id']}/imagen?v={int(cap.timestamp())}" if cap else None
    # Comentarios (los muestra el muro del grupo).
    ids = [f["id"] for f in filas]
    comentarios = {}
    if ids:
        for c in _leer("""
            SELECT c.id, c.nota_id, c.usuario_id, us.nombre AS autor, c.creada, c.texto, c.responde_a
            FROM nota_comentario c JOIN usuario us ON us.id = c.usuario_id
            WHERE c.nota_id = ANY(%s) ORDER BY c.creada""", (ids,)):
            c["mio"] = c.pop("usuario_id") == u["id"]
            comentarios.setdefault(c.pop("nota_id"), []).append(c)
    reac = _reacciones(u, [f"nota:{f['id']}" for f in filas])
    for f in filas:
        f["comentarios"] = comentarios.get(f["id"], [])
        f["reacciones"] = reac.get(f"nota:{f['id']}", {"conteo": {}, "mias": [], "quienes": {}})
        f["insignias"] = _insignia_nota(f)
    return filas


# ---------------------------------------------------------------------------
# Red del grupo: reacciones, comentarios en los pronósticos, perfil y el muro
# ---------------------------------------------------------------------------
TIPOS_REACCION = ("util", "acuerdo", "duda", "aplauso")


def _reacciones(u, objetos):
    """{objeto: {"conteo": {tipo: n}, "mias": [tipos], "quienes": {tipo: [nombres]}}}"""
    if not objetos:
        return {}
    salida = {}
    for r in _leer("""SELECT r.objeto, r.tipo, r.usuario_id, us.nombre FROM reaccion r JOIN usuario us ON us.id = r.usuario_id
                      WHERE r.objeto = ANY(%s) ORDER BY r.creada""", (list(objetos),)):
        x = salida.setdefault(r["objeto"], {"conteo": {}, "mias": [], "quienes": {}})
        x["conteo"][r["tipo"]] = x["conteo"].get(r["tipo"], 0) + 1
        x["quienes"].setdefault(r["tipo"], []).append(r["nombre"])
        if r["usuario_id"] == u["id"]:
            x["mias"].append(r["tipo"])
    return salida


def objeto_pronostico(ciudad, fecha, firma):
    return f"pron|{ciudad}|{fecha}|{firma}"


def _existe_objeto(u, objeto):
    """¿Existe y lo puede ver? (una nota visible o una publicación de pronóstico)."""
    if objeto.startswith("nota:") and objeto[5:].isdigit():
        return bool(_leer("SELECT 1 FROM nota WHERE id = %s AND (compartida OR usuario_id = %s OR %s)",
                          (int(objeto[5:]), u["id"], u["rol"] == "admin")))
    partes = objeto.split("|")
    if len(partes) == 4 and partes[0] == "pron":
        try:
            fecha = date.fromisoformat(partes[2])
        except ValueError:
            return False
        return bool(_leer("SELECT 1 FROM pronostico_emision WHERE ciudad = %s AND fecha_pronostico = %s AND publicado_por = %s LIMIT 1",
                          (partes[1], fecha, partes[3])))
    return False


@router.post("/api/yo/reacciones")
async def reaccionar(request: Request):
    """Poner o sacar una reacción (es un interruptor)."""
    u = requiere_usuario(request)
    datos = await request.json()
    objeto, tipo = str(datos.get("objeto") or "")[:300], datos.get("tipo")
    if tipo not in TIPOS_REACCION:
        raise HTTPException(400, f"tipo: uno de {', '.join(TIPOS_REACCION)}")
    if not _existe_objeto(u, objeto):
        raise HTTPException(404, "No existe o no lo podés ver")
    if not _escribir("DELETE FROM reaccion WHERE objeto = %s AND usuario_id = %s AND tipo = %s RETURNING 1", (objeto, u["id"], tipo)):
        _escribir("INSERT INTO reaccion (objeto, usuario_id, tipo) VALUES (%s, %s, %s)", (objeto, u["id"], tipo))
    return _reacciones(u, [objeto]).get(objeto, {"conteo": {}, "mias": [], "quienes": {}})


@router.post("/api/yo/pronostico/comentarios")
async def comentar_pronostico(request: Request):
    u = requiere_usuario(request)
    datos = await request.json()
    objeto, texto = str(datos.get("objeto") or "")[:300], str(datos.get("texto") or "").strip()
    if not 1 <= len(texto) <= 2000:
        raise HTTPException(400, "El comentario tiene que tener entre 1 y 2.000 caracteres")
    if not objeto.startswith("pron|") or not _existe_objeto(u, objeto):
        raise HTTPException(404, "Ese pronóstico no existe")
    responde = _responde_a(datos, "SELECT 1 FROM comentario_pronostico WHERE id = %s AND objeto = %s", objeto)
    return _escribir("INSERT INTO comentario_pronostico (objeto, usuario_id, texto, responde_a) VALUES (%s, %s, %s, %s) RETURNING id",
                     (objeto, u["id"], texto, responde))[0]


@router.delete("/api/yo/pronostico/comentarios/{comentario_id}")
def borrar_comentario_pronostico(comentario_id: int, request: Request):
    u = requiere_usuario(request)
    if not _escribir("DELETE FROM comentario_pronostico WHERE id = %s AND (usuario_id = %s OR %s) RETURNING id",
                     (comentario_id, u["id"], u["rol"] == "admin")):
        raise HTTPException(404, "No existe o no es tuyo")
    return {"ok": True}


@router.put("/api/yo/perfil")
async def editar_perfil(request: Request):
    """La presentación propia y si se muestran las métricas al grupo."""
    u = requiere_usuario(request)
    datos = await request.json()
    bio = str(datos.get("bio") or "").strip()[:500] or None
    _escribir("UPDATE usuario SET bio = %s, metricas_publicas = %s WHERE id = %s",
              (bio, bool(datos.get("metricas_publicas")), u["id"]))
    return {"ok": True}


HOY_ARG = lambda: datetime.now(timezone(timedelta(hours=-3))).date()

# Insignias (solo cosas buenas: para incentivar, no para exponer a nadie).
INSIGNIAS = {
    "clavadas": ("🎯", "Temperaturas clavadas", "todas las temperaturas verificadas del pronóstico a ±1 °C o menos (al menos 2)"),
    "lluvia": ("☔", "Acertó la lluvia", "acertó si llovía o no en todos los días verificados (al menos 2)"),
    "kamikaze": ("🔮", "Kamikaze", "se apartó 3 °C o más del GFS y acertó (±1 °C) donde el modelo erró por 2,5 °C o más"),
    "halcon": ("🦅", "Ojo de halcón", "una nota de lo que vio con 3 o más 👍 Útil de otros"),
}
MIN_UTIL_HALCON = 3


def _insignias_pron(dias):
    """dias: [{"tmin": {"pron", "obs"}, "tmax": ..., "pp": {"pron", "obs"}, "gfs": {"tmin", "tmax"} | None}]"""
    ok = lambda v: v is not None
    errs = [abs(d[k]["obs"] - d[k]["pron"]) for d in dias for k in ("tmin", "tmax")
            if d.get(k) and ok(d[k].get("pron")) and ok(d[k].get("obs"))]
    pp = [d["pp"] for d in dias if d.get("pp") and ok(d["pp"].get("pron")) and ok(d["pp"].get("obs"))]
    salida = []
    if len(errs) >= 2 and all(e <= 1 for e in errs):
        salida.append("clavadas")
    if len(pp) >= 2 and all(x["pron"] == x["obs"] for x in pp):
        salida.append("lluvia")
    for d in dias:
        g = d.get("gfs") or {}
        if any(d.get(k) and ok(d[k].get("pron")) and ok(d[k].get("obs")) and ok(g.get(k))
               and abs(d[k]["pron"] - g[k]) >= 3 and abs(d[k]["obs"] - d[k]["pron"]) <= 1 and abs(d[k]["obs"] - g[k]) >= 2.5
               for k in ("tmin", "tmax")):
            salida.append("kamikaze")
            break
    return salida


def _insignia_nota(n):
    util_de_otros = [x for x in (n.get("reacciones") or {}).get("quienes", {}).get("util", []) if x != n.get("autor")]
    return ["halcon"] if n["tipo"] in ("vi", "llamo_atencion") and len(util_de_otros) >= MIN_UTIL_HALCON else []
_MODELO_CACHE = {}


def _gfs_diario(ciudad, capturado):
    """La mínima y la máxima del GFS para cada día (argentino) en la ciudad,
    según la corrida que estaba disponible al publicar (el GFS sale ~5 h
    después de su hora de inicio; si no está guardada, las anteriores).
    Con los valores cada 6 h (03, 09, 15 y 21 UTC): da una idea del modelo,
    no sus extremos exactos. -> ({fecha: {"tmin", "tmax"}}, "AAAAMMDDHH") o ({}, None)"""
    from ..cartas import puntos
    t = capturado.astimezone(timezone.utc) - timedelta(hours=5)
    inicio = t.replace(hour=t.hour - t.hour % 6, minute=0, second=0, microsecond=0)
    for _ in range(4):
        clave = inicio.strftime("%Y%m%d%H")
        if clave not in _MODELO_CACHE:
            if len(_MODELO_CACHE) > 400:
                _MODELO_CACHE.clear()
            _MODELO_CACHE[clave] = puntos.leer_pronostico(ARCHIVO_DIR, inicio)
        d = _MODELO_CACHE[clave]
        if d and d.get("plazos"):
            dias = {}
            for plazo, ciudades in d["plazos"].items():
                t2 = (ciudades.get(ciudad) or {}).get("sup", {}).get("t")
                if t2 is None:
                    continue
                dia = (inicio + timedelta(hours=int(plazo)) - timedelta(hours=3)).date()     # día argentino
                x = dias.setdefault(dia, {"tmin": t2, "tmax": t2})
                x["tmin"], x["tmax"] = min(x["tmin"], t2), max(x["tmax"], t2)
            return dias, clave
        inicio -= timedelta(hours=6)
    return {}, None


@router.get("/api/yo/personas")
def personas(request: Request):
    """Quiénes están en el grupo: cada usuario (y las firmas sin usuario), con
    su actividad de los últimos 90 días y, si se pueden ver, sus números."""
    from ..verificacion import puntajes
    u = requiere_usuario(request)
    hasta = HOY_ARG() - timedelta(days=1)
    desde = hasta - timedelta(days=90)
    pares = _pares(_vigentes(desde, hasta), desde, hasta)
    # Insignias de los 90 días, por firma (con el GFS que había al publicar cada uno).
    capturas = {(e["ciudad"], e["fecha_pronostico"], e["publicado_por"]): e["capturado"] for e in _leer("""
        SELECT ciudad, fecha_pronostico, publicado_por, max(capturado) AS capturado FROM pronostico_emision
        WHERE fecha_pronostico BETWEEN %s AND %s GROUP BY 1, 2, 3""", (desde - timedelta(days=4), hasta))}
    por_emision = {}
    for f in pares:
        por_emision.setdefault((f["ciudad"], f["fecha_emision"], f["pronosticador"]), []).append(f)
    ins_firma = {}
    for clave, filas in por_emision.items():
        cap = capturas.get(clave)
        modelo = _gfs_diario(clave[0], cap)[0] if cap else {}
        dias = [{"tmin": f["tmin_urbana"], "tmax": f["tmax_urbana"], "pp": f["pp"], "gfs": modelo.get(f["fecha"])} for f in filas]
        for i in _insignias_pron(dias):
            c = ins_firma.setdefault(clave[2], {})
            c[i] = c.get(i, 0) + 1
    halcon = {r["usuario_id"]: r["n"] for r in _leer(f"""
        SELECT n.usuario_id, count(*) AS n FROM nota n
        WHERE n.tipo IN ('vi', 'llamo_atencion') AND n.compartida AND n.creada::date >= %s
          AND (SELECT count(*) FROM reaccion r WHERE r.objeto = 'nota:' || n.id AND r.tipo = 'util' AND r.usuario_id <> n.usuario_id) >= {MIN_UTIL_HALCON}
        GROUP BY 1""", (desde,))}
    notas_n = {r["usuario_id"]: r["n"] for r in _leer(
        "SELECT usuario_id, count(*) AS n FROM nota WHERE compartida OR usuario_id = %s GROUP BY usuario_id", (u["id"],))}
    salida, con_usuario = [], set()
    for p in _leer("SELECT id, nombre, rol, firmas, bio, metricas_publicas, ultimo_ingreso FROM usuario WHERE activo ORDER BY nombre"):
        mias = [f for f in pares if f["pronosticador"] in p["firmas"]]
        con_usuario.update(p["firmas"])
        ver = puede_ver_metricas(u, p)
        ins = {}
        for f in p["firmas"]:
            for k, n in ins_firma.get(f, {}).items():
                ins[k] = ins.get(k, 0) + n
        if halcon.get(p["id"]):
            ins["halcon"] = halcon[p["id"]]
        salida.append({**p, "insignias": ins, "notas": notas_n.get(p["id"], 0), "dias_pronosticados": len(mias),
                       "emisiones": len({(f["ciudad"], f["fecha_emision"]) for f in mias}),
                       "metricas_visibles": ver, "resumen": puntajes.resumen(mias) if ver and mias else None,
                       "yo": p["id"] == u["id"]})
    for firma in sorted({f["pronosticador"] for f in pares if f["pronosticador"]} - con_usuario):
        mias = [f for f in pares if f["pronosticador"] == firma]
        ver = es_coordinador(u)
        salida.append({"id": None, "nombre": firma, "rol": None, "firmas": [firma], "bio": None, "sin_cuenta": True,
                       "insignias": ins_firma.get(firma, {}),
                       "notas": 0, "dias_pronosticados": len(mias), "emisiones": len({(f["ciudad"], f["fecha_emision"]) for f in mias}),
                       "metricas_visibles": ver, "resumen": puntajes.resumen(mias) if ver else None, "yo": False})
    return {"desde": desde, "hasta": hasta, "personas": salida,
            "insignias": {k: {"emoji": e, "nombre": n, "que": q} for k, (e, n, q) in INSIGNIAS.items()}}


@router.get("/api/yo/muro")
def muro(request: Request, hasta: date | None = None, dias: int = 7, autor_id: int | None = None, firma: str = ""):
    """El inicio de la red: notas compartidas (y las propias) y pronósticos
    publicados, con lo observado, de `dias` días hasta `hasta` (lo más nuevo
    primero). Con autor_id (o firma), solo lo de esa persona."""
    u = requiere_usuario(request)
    hasta = hasta or HOY_ARG()
    dias = max(1, min(dias, 31))
    desde = hasta - timedelta(days=dias - 1)
    firmas = None
    if autor_id:
        p = _leer("SELECT firmas FROM usuario WHERE id = %s", (autor_id,))
        if not p:
            raise HTTPException(404, "usuario desconocido")
        firmas = set(p[0]["firmas"])
    elif firma:
        firmas = {firma}
    items = []
    # Notas: por el día en que se escribieron.
    if not firma:
        cond = ["(n.usuario_id = %s OR n.compartida)",
                "(n.creada AT TIME ZONE 'America/Argentina/Buenos_Aires')::date BETWEEN %s AND %s"]
        params = [u["id"], desde, hasta]
        if autor_id:
            cond.append("n.usuario_id = %s"); params.append(autor_id)
        for n in _notas_con_extras(u, cond, params, limite=300):
            items.append({"tipo": "nota", "ts": n["creada"], "nota": n})
    # Pronósticos: los publicados en el período (con sus 4 días, aunque los
    # últimos todavía no tengan observación).
    emisiones = _leer("""
        SELECT ciudad, fecha_pronostico, publicado_por, max(capturado) AS capturado, bool_or(es_correccion) AS correccion
        FROM pronostico_emision WHERE fecha_pronostico BETWEEN %s AND %s AND publicado_por IS NOT NULL
        GROUP BY 1, 2, 3""", (desde, hasta))
    if firmas is not None:
        emisiones = [e for e in emisiones if e["publicado_por"] in firmas]
    if emisiones:
        pares = _pares(_vigentes(desde, hasta + timedelta(days=6)), desde, hasta + timedelta(days=6))
        usuarios = {f: x for x in _leer("SELECT id, nombre, firmas FROM usuario WHERE activo") for f in x["firmas"]}
        por_emision = {}
        for f in pares:
            por_emision.setdefault((f["ciudad"], f["fecha_emision"], f["pronosticador"]), []).append(f)
        objetos = [objeto_pronostico(e["ciudad"], e["fecha_pronostico"], e["publicado_por"]) for e in emisiones]
        reac = _reacciones(u, objetos)
        coment = {}
        for c in _leer("""SELECT c.id, c.objeto, c.usuario_id, us.nombre AS autor, c.creada, c.texto, c.responde_a
                          FROM comentario_pronostico c JOIN usuario us ON us.id = c.usuario_id
                          WHERE c.objeto = ANY(%s) ORDER BY c.creada""", (objetos,)):
            c["mio"] = c.pop("usuario_id") == u["id"]
            coment.setdefault(c.pop("objeto"), []).append(c)
        for e, obj in zip(emisiones, objetos):
            filas = sorted(por_emision.get((e["ciudad"], e["fecha_pronostico"], e["publicado_por"]), []), key=lambda f: f["plazo"])
            if not filas:
                continue        # todos sus días los corrigió otro: aparece la corrección
            us = usuarios.get(e["publicado_por"])
            modelo, corrida = _gfs_diario(e["ciudad"], e["capturado"])
            items.append({"tipo": "pronostico", "corrida_gfs": corrida, "ts": e["capturado"], "objeto": obj, "ciudad": e["ciudad"],
                          "fecha_emision": e["fecha_pronostico"], "firma": e["publicado_por"], "correccion": e["correccion"],
                          "usuario": {"id": us["id"], "nombre": us["nombre"]} if us else None,
                          "anticipado": any(f["anticipado"] for f in filas),
                          "dias": [{"fecha": f["fecha"], "plazo": f["plazo"], "tmin": f["tmin_urbana"], "tmax": f["tmax_urbana"],
                                    "pp": {"pron": f["pp"]["pron"], "obs": f["pp"]["obs"], "tipo": f["pp"]["tipo"]},
                                    "gfs": modelo.get(f["fecha"])} for f in filas],
                          "reacciones": reac.get(obj, {"conteo": {}, "mias": [], "quienes": {}}),
                          "comentarios": coment.get(obj, [])})
            items[-1]["insignias"] = _insignias_pron(items[-1]["dias"])
    items.sort(key=lambda x: x["ts"], reverse=True)
    # ¿Hay algo más viejo? (para el botón "Ver más")
    mas_viejo = _leer("""SELECT least((SELECT min(fecha_pronostico) FROM pronostico_emision),
                                      (SELECT min(creada)::date FROM nota)) AS f""")[0]["f"]
    return {"desde": desde, "hasta": hasta, "items": items,
            "siguiente": (desde - timedelta(days=1)) if mas_viejo and mas_viejo < desde else None}


ARCHIVO_DIR = os.environ.get("ARCHIVO_DIR", "/archivo")


def _miniatura(c):
    """La imagen de la carta donde se escribió la nota (para el muro), o None:
    la carta de análisis (su miniatura, o la imagen) o la del pronóstico GFS."""
    from ..cartas import archivo, pronostico
    try:
        if c.get("seccion") == "gfs" and c.get("carta") and c.get("corrida"):
            nombre = f"{c['carta']}_{c['corrida']}_f{int(c['plazo']):03d}.png"
            ruta = pronostico.carpeta(ARCHIVO_DIR, c["carta"]) / nombre
            return f"/cartas-img/{pronostico.CARPETA}/{c['carta']}/{nombre}" if ruta.exists() else None
        if c.get("ts"):
            ts = datetime.fromisoformat(str(c["ts"]).replace("Z", "+00:00"))
            ts = ts.replace(hour=ts.hour - ts.hour % 6, minute=0, second=0, microsecond=0)
            raiz = Path(ARCHIVO_DIR) / "cartas"
            for producto in [c.get("producto"), "gfs_ana_mslp_jet", "bentley_mslp_jet"]:
                if not producto:
                    continue
                m = archivo.ruta_miniatura(ARCHIVO_DIR, producto, ts)
                if m.exists():
                    return "/cartas-img/" + m.relative_to(raiz).as_posix()
                base = archivo.ruta(ARCHIVO_DIR, producto, ts)
                for ext in ("png", "jpg", "gif"):
                    if base.with_suffix(f".{ext}").exists():
                        return "/cartas-img/" + base.with_suffix(f".{ext}").relative_to(raiz).as_posix()
    except (ValueError, TypeError, KeyError):
        return None
    return None


@router.post("/api/yo/notas")
async def nueva_nota(request: Request):
    u = requiere_usuario(request)
    datos = await request.json()
    tipo, texto, etiquetas, compartida, contexto, fecha = _limpiar_nota(datos)
    imagen = _preparar_imagen(datos)          # antes: que una imagen mala no deje la nota a medias
    fila = _escribir("""
        INSERT INTO nota (usuario_id, tipo, texto, etiquetas, compartida, contexto, fecha_evento)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                     (u["id"], tipo, texto, etiquetas, compartida, Jsonb(contexto), fecha))[0]
    _guardar_imagen(fila["id"], imagen)
    return fila


@router.put("/api/yo/notas/{nota_id}")
async def editar_nota(nota_id: int, request: Request):
    u = requiere_usuario(request)
    datos = await request.json()
    tipo, texto, etiquetas, compartida, contexto, fecha = _limpiar_nota(datos)
    imagen = _preparar_imagen(datos)
    filas = _escribir("""
        UPDATE nota SET tipo = %s, texto = %s, etiquetas = %s, compartida = %s, contexto = %s, fecha_evento = %s,
                        actualizada = now()
        WHERE id = %s AND usuario_id = %s RETURNING id""",
                      (tipo, texto, etiquetas, compartida, Jsonb(contexto), fecha, nota_id, u["id"]))
    if not filas:
        raise HTTPException(404, "No existe o no es tuya")
    _guardar_imagen(nota_id, imagen)
    return filas[0]


@router.get("/api/yo/notas/{nota_id}/imagen")
def imagen_nota(nota_id: int, request: Request):
    """La imagen pegada a la nota: solo para quien puede ver la nota."""
    u = requiere_usuario(request)
    filas = _leer("""SELECT ni.datos FROM nota_imagen ni JOIN nota n ON n.id = ni.nota_id
                     WHERE ni.nota_id = %s AND (n.compartida OR n.usuario_id = %s OR %s)""",
                  (nota_id, u["id"], u["rol"] == "admin"))
    if not filas:
        raise HTTPException(404, "No existe o no la podés ver")
    return Response(bytes(filas[0]["datos"]), media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.put("/api/yo/notas/{nota_id}/reunion")
async def tratar_en_reunion(nota_id: int, request: Request):
    """Marcar una pregunta como tratada en la reunión y anotar la conclusión:
    quien la escribió o un coordinador."""
    u = requiere_usuario(request)
    datos = await request.json()
    respuesta = str(datos.get("respuesta") or "").strip()[:5000] or None
    filas = _escribir("""
        UPDATE nota SET tratada = %s, respuesta = %s, tratada_por = %s
        WHERE id = %s AND compartida AND (usuario_id = %s OR %s) RETURNING id""",
                      (bool(datos.get("tratada")), respuesta, u["id"], nota_id, u["id"], es_coordinador(u)))
    if not filas:
        raise HTTPException(404, "No existe, no es compartida o no la podés modificar")
    return filas[0]


@router.post("/api/yo/notas/{nota_id}/comentarios")
async def comentar(nota_id: int, request: Request):
    """Comentar una nota compartida (o una propia)."""
    u = requiere_usuario(request)
    datos = await request.json()
    texto = str(datos.get("texto") or "").strip()
    if not 1 <= len(texto) <= 2000:
        raise HTTPException(400, "El comentario tiene que tener entre 1 y 2.000 caracteres")
    if not _leer("SELECT 1 FROM nota WHERE id = %s AND (compartida OR usuario_id = %s OR %s)",
                 (nota_id, u["id"], u["rol"] == "admin")):
        raise HTTPException(404, "No existe o no la podés ver")
    responde = _responde_a(datos, "SELECT 1 FROM nota_comentario WHERE id = %s AND nota_id = %s", nota_id)
    return _escribir("INSERT INTO nota_comentario (nota_id, usuario_id, texto, responde_a) VALUES (%s, %s, %s, %s) RETURNING id",
                     (nota_id, u["id"], texto, responde))[0]


def _responde_a(datos, sql, dueño):
    """El comentario al que responde (tiene que ser del mismo hilo) o None."""
    r = datos.get("responde_a")
    if r in (None, ""):
        return None
    if not isinstance(r, int) or not _leer(sql, (r, dueño)):
        raise HTTPException(400, "responde_a: ese comentario no es de este hilo")
    return r


@router.delete("/api/yo/comentarios/{comentario_id}")
def borrar_comentario(comentario_id: int, request: Request):
    u = requiere_usuario(request)
    if not _escribir("DELETE FROM nota_comentario WHERE id = %s AND (usuario_id = %s OR %s) RETURNING id",
                     (comentario_id, u["id"], u["rol"] == "admin")):
        raise HTTPException(404, "No existe o no es tuyo")
    return {"ok": True}


@router.delete("/api/yo/notas/{nota_id}")
def borrar_nota(nota_id: int, request: Request):
    u = requiere_usuario(request)
    filas = _escribir("DELETE FROM nota WHERE id = %s AND (usuario_id = %s OR %s) RETURNING id",
                      (nota_id, u["id"], u["rol"] == "admin"))
    if not filas:
        raise HTTPException(404, "No existe o no es tuya")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Alta y baja de usuarios (desde la terminal del servidor)
# ---------------------------------------------------------------------------
def main(argv=None):
    """docker compose exec web python -m meteo.web.cuentas agregar --email ... --nombre ... [--firma Mate] [--rol coordinador]
    docker compose exec web python -m meteo.web.cuentas listar
    docker compose exec web python -m meteo.web.cuentas desactivar --email ..."""
    import psycopg
    from psycopg.rows import dict_row
    ap = argparse.ArgumentParser(prog="python -m meteo.web.cuentas", description=main.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["agregar", "listar", "desactivar", "activar"])
    ap.add_argument("--email")
    ap.add_argument("--nombre")
    ap.add_argument("--rol", choices=["pronosticador", "coordinador", "admin"], default="pronosticador")
    ap.add_argument("--firma", action="append", default=[],
                    help="cómo firma los pronósticos en el portal (se puede repetir)")
    a = ap.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL", "postgresql://meteo:meteo@db:5432/meteo")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(SQL.read_text(encoding="utf-8"))
        if a.comando == "listar":
            for u in conn.execute("SELECT * FROM usuario ORDER BY nombre"):
                print(f"{u['email']:35} {u['nombre']:20} {u['rol']:13} firmas={','.join(u['firmas']) or '-':20} "
                      f"{'activo' if u['activo'] else 'INACTIVO'}  último ingreso: {u['ultimo_ingreso'] or '-'}")
            return
        if not a.email:
            ap.error("falta --email")
        email = a.email.strip().lower()
        if a.comando == "agregar":
            if not a.nombre:
                ap.error("falta --nombre")
            conn.execute("""
                INSERT INTO usuario (email, nombre, rol, firmas) VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET nombre = EXCLUDED.nombre, rol = EXCLUDED.rol,
                    firmas = EXCLUDED.firmas, activo = true""", (email, a.nombre, a.rol, a.firma))
            print(f"listo: {email} ({a.rol}) puede ingresar")
        else:
            activo = a.comando == "activar"
            n = conn.execute("UPDATE usuario SET activo = %s WHERE email = %s", (activo, email)).rowcount
            if not activo:
                conn.execute("DELETE FROM sesion WHERE usuario_id IN (SELECT id FROM usuario WHERE email = %s)", (email,))
            print(f"{'activado' if activo else 'desactivado (y se cerraron sus sesiones)'}: {email}" if n else "no existe ese mail")


if __name__ == "__main__":
    sys.exit(main())
