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
- Permisos: las notas privadas solo las ve quien las escribió; las
  compartidas, todos los que ingresaron. Las métricas de cada uno, él y los
  coordinadores.

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
            SELECT u.id, u.email, u.nombre, u.rol, u.firmas FROM sesion s JOIN usuario u ON u.id = s.usuario_id
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


@router.get("/api/yo/pronosticos")
def mis_pronosticos(request: Request, desde: date | None = None, hasta: date | None = None, usuario_id: int | None = None):
    """Los pronósticos del usuario (por su firma) contra lo observado, y sus
    medidas de error por plazo (las mismas de la pestaña Verificación).
    Un coordinador puede pedir los de otro (usuario_id)."""
    from ..verificacion import puntajes
    u = requiere_usuario(request)
    quien = u
    if usuario_id and usuario_id != u["id"]:
        if not es_coordinador(u):
            raise HTTPException(403, "Solo los coordinadores ven las métricas de otros")
        filas = _leer("SELECT id, email, nombre, rol, firmas FROM usuario WHERE id = %s", (usuario_id,))
        if not filas:
            raise HTTPException(404, "usuario desconocido")
        quien = filas[0]
    hasta = hasta or datetime.now(timezone(timedelta(hours=-3))).date() - timedelta(days=1)
    desde = desde or hasta - timedelta(days=90)
    if desde > hasta or (hasta - desde).days > 3 * 366:
        raise HTTPException(400, "Rango de fechas inválido (máximo 3 años)")
    salida = {"usuario": {k: quien[k] for k in ("id", "nombre", "firmas")}, "desde": desde, "hasta": hasta,
              "resumen": {}, "dias": []}
    if not quien["firmas"]:
        return salida
    # Primero la versión vigente de cada pronóstico (de quien sea) y DESPUÉS
    # las de esta persona: si otro corrigió su pronóstico, cuenta la corrección.
    dias = _leer("""
        SELECT e.ciudad, e.fecha_pronostico, e.capturado, e.publicado_por, e.es_correccion,
               COALESCE((e.contenido -> 'publicado_el' ->> 0)::date,
                        (e.capturado AT TIME ZONE 'America/Argentina/Buenos_Aires')::date) AS publicado_el,
               d.fecha, d.plazo_dias, d.temp_min, d.temp_max, d.temp_min_suburbana, d.temp_max_suburbana, d.periodos
        FROM pronostico_dia d JOIN pronostico_emision e ON e.id = d.emision_id
        WHERE d.fecha BETWEEN %s AND %s""", (desde, hasta))
    vigentes = [d for d in puntajes.elegir_emisiones(dias, "final") if d["publicado_por"] in quien["firmas"]]
    ciudades = sorted({d["ciudad"] for d in vigentes})
    observado = {(r["ciudad"], r["fecha"], r["elemento"]): r for r in _leer("""
        SELECT ciudad, fecha, elemento, valor, categoria, estacion_id, detalle FROM verif_observado
        WHERE ciudad = ANY(%s) AND fecha BETWEEN %s AND %s""", (ciudades, desde - timedelta(days=5), hasta))}
    filas = puntajes.armar_pares(vigentes, observado)
    filas.sort(key=lambda f: (f["fecha"], f["ciudad"], f["plazo"]))
    salida["resumen"] = puntajes.resumen(filas)
    salida["dias"] = filas
    return salida


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


@router.get("/api/yo/notas")
def notas(request: Request, desde: date | None = None, hasta: date | None = None, tipo: str = "",
          mias: bool = False, pendientes: bool = False, etiqueta: str = ""):
    """Las notas propias y las compartidas por los demás (con su autor)."""
    u = requiere_usuario(request)
    cond, params = ["(n.usuario_id = %s OR n.compartida)"], [u["id"]]
    if mias:
        cond.append("n.usuario_id = %s"); params.append(u["id"])
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
    filas = _leer(f"""
        SELECT n.id, n.usuario_id, us.nombre AS autor, n.creada, n.actualizada, n.tipo, n.texto, n.etiquetas,
               n.compartida, n.fecha_evento, n.contexto, n.tratada, n.respuesta, tr.nombre AS tratada_por
        FROM nota n JOIN usuario us ON us.id = n.usuario_id LEFT JOIN usuario tr ON tr.id = n.tratada_por
        WHERE {' AND '.join(cond)} ORDER BY COALESCE(n.fecha_evento, n.creada::date) DESC, n.creada DESC LIMIT 500""",
                  tuple(params))
    for f in filas:
        f["mia"] = f.pop("usuario_id") == u["id"]
    return filas


@router.post("/api/yo/notas")
async def nueva_nota(request: Request):
    u = requiere_usuario(request)
    tipo, texto, etiquetas, compartida, contexto, fecha = _limpiar_nota(await request.json())
    return _escribir("""
        INSERT INTO nota (usuario_id, tipo, texto, etiquetas, compartida, contexto, fecha_evento)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                     (u["id"], tipo, texto, etiquetas, compartida, Jsonb(contexto), fecha))[0]


@router.put("/api/yo/notas/{nota_id}")
async def editar_nota(nota_id: int, request: Request):
    u = requiere_usuario(request)
    tipo, texto, etiquetas, compartida, contexto, fecha = _limpiar_nota(await request.json())
    filas = _escribir("""
        UPDATE nota SET tipo = %s, texto = %s, etiquetas = %s, compartida = %s, contexto = %s, fecha_evento = %s,
                        actualizada = now()
        WHERE id = %s AND usuario_id = %s RETURNING id""",
                      (tipo, texto, etiquetas, compartida, Jsonb(contexto), fecha, nota_id, u["id"]))
    if not filas:
        raise HTTPException(404, "No existe o no es tuya")
    return filas[0]


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
