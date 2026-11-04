# 7. Cuentas de los pronosticadores (perfil, métricas y libreta)

La página de Cartas (también la copia pública) sigue igual para todos. Quien
**ingresa con Google** (y está en la lista del grupo) ve además:

- **Mi perfil → Mis pronósticos**: sus pronósticos contra lo observado y sus
  errores por plazo (MAE y sesgo de mínima y máxima, aciertos de lluvia y de
  dirección del viento), con las mismas cuentas que la pestaña Verificación.
  Se cuenta la **versión vigente** de cada pronóstico: si otro lo corrigió,
  ese día es de quien lo corrigió. Los coordinadores pueden ver los de cada uno.
- **Mi perfil es una red del grupo**, con un menú a la izquierda:
  - **Inicio**: lo que publicó el grupo, lo más nuevo arriba y por día. Mezcla
    las **notas** (lo que vi, me llamó la atención, dudas, preguntas) con los
    **pronósticos publicados**: cada uno muestra sus 4 días, lo pronosticado
    contra lo observado (con los colores del error) y, cuando corresponde,
    insignias ("🎯 temperaturas clavadas", "☔ acertó la lluvia"). Se
    reacciona (👍 útil, 🤝 de acuerdo, 🤔 dudas; 👏 en los pronósticos) y se
    comenta. Arriba se escribe (privada con 🔒); filtros por tipo y buscador
    (texto, persona, #etiqueta, ciudad); "Ver días anteriores" va hacia atrás.
    Tocando un día del pronóstico (🗺️) o "Cartas del día" se abren las cartas
    de diagnóstico de esa fecha, para ver la situación; el botón flotante
    "← Volver al pronóstico" deja de nuevo en esa tarjeta, listo para comentar.
    **📈 Ver el modelo** abre en la tarjeta un gráfico con la temperatura a 2 m
    del GFS de la corrida que había cuando se publicó (la de ~5 h antes), la
    mínima y la máxima pronosticadas (◇) y las observadas (●), y la lluvia del
    modelo. **🌐 Cartas del GFS** abre las cartas y el meteograma de esa
    corrida para esa ciudad. Las corridas se guardan 8 días: para pronósticos
    más viejos lo avisa.
  - **Videos y links**: los links que se pegan en una nota o un comentario
    se pueden tocar. Los de **Instagram, YouTube, TikTok, X, Facebook y
    Vimeo** muestran una tarjeta "▶ Ver el video": el reproductor de ese
    sitio se carga recién al tocarla (la página no se pone lenta y esos
    sitios no reciben nada de quien solo mira el muro). Solo se ven las
    publicaciones públicas.
  - **Compartir desde el celular**: la página se puede instalar como app
    (Chrome en Android: menú ⋮ → "Instalar app" o "Agregar a la pantalla de
    inicio", desde la dirección https). Instalada, aparece en el menú
    **Compartir** de Instagram, YouTube, etc. ("Meteo UNLP"): el link llega
    solo al cuadro de escribir del Inicio. Sin instalarla (o en iPhone, que
    no lo permite): "Copiar enlace" en la app y **📋 Pegar link** en la página.
  - **Perfil** de cada persona (tocando su nombre o avatar): presentación,
    sus números (si se pueden ver), sus publicaciones y la pestaña
    **Pronósticos y errores** (la tabla por plazo y las placas día por día).
    En el propio, "Editar perfil": la presentación y si se muestran los
    números al grupo.
  - **El grupo**: todos, incluidas las firmas sin cuenta.
  - **Reunión sinóptica**: las preguntas, pendientes arriba.
  - **Métricas del grupo** (coordinadores): la tabla de todas las firmas.
  - A la derecha: lo de **ayer, pronosticado contra observado**, y las
    preguntas pendientes.

  **Qué ve cada uno**: los pronósticos día por día con lo observado son
  públicos (ya lo eran en Verificación). Los **números** de cada persona
  (errores medios, aciertos) los ven ella, los coordinadores y, si ella lo
  elige en su perfil (🔓), todo el grupo. Las notas privadas, su autor y los
  administradores.
- **Capas no destructivas**: la pizarra guarda la imagen de base (la
  carta, el meteograma) y, aparte, el dibujo como lista de elementos (lápiz,
  A/B, símbolos de tiempo presente, y lo dibujado y medido en el visor). En
  el muro, **👁 Ocultar/Mostrar dibujo** deja ver la carta original; al
  editar la nota, el dibujo sigue siendo editable.
- **Etiquetas por zona**: al anotar desde el visor de una carta con
  coordenadas, la nota propone etiquetas según dónde cayó lo que se dibujó
  o midió (región pampeana, litoral, costa atlántica, río de la plata,
  patagonia, sur de brasil, atlántico sur…). Se pueden cambiar.
- **La pizarra**: al anotar desde el visor de una carta, el pronóstico GFS o
  un meteograma, la nota **pega la imagen** de lo que se estaba mirando (la
  carta con lo que se dibujó encima, el meteograma completo) y se puede
  **marcar encima** con el lápiz (rojo, azul, verde, negro). También se puede
  subir una imagen propia (📎). Esa imagen queda en la nota: en el muro la
  ven todos los que pueden ver la nota. Se guarda en la base (tabla
  `nota_imagen`, en WebP, hasta 1600 px), no en una carpeta pública: solo
  la entrega `/api/yo/notas/{id}/imagen` a quien tiene sesión y permiso.
- **Coordinadores y administradores**: la tabla **"Todo el grupo"** (una
  fila por firma, aunque esa persona no tenga usuario: MAE de mínima y
  máxima del mismo día y del día siguiente, aciertos de lluvia) con "Ver
  detalle", el selector "De:" (por usuario o por firma) y el filtro de
  autor en el muro. Los **administradores** además pueden ver las notas
  privadas de todos ("Incluir las privadas de todos").

Código: `app/meteo/web/cuentas.py` (y `cuentas.sql`, que la web crea sola).

## Seguridad

- **Quién entra**: Google dice quién es (mail verificado); la tabla `usuario`
  dice si puede pasar. Solo los mails cargados con el comando de abajo. No
  se guarda ninguna contraseña.
- **El ingreso**: OAuth de Google con `state` y PKCE; el canje del código y
  la consulta del mail van servidor a servidor por HTTPS.
- **La sesión**: un token aleatorio en una cookie HttpOnly, Secure y
  SameSite=Lax, que vence a los 30 días. En la base se guarda solo su huella
  (sha256): quien leyera la base no podría usar las sesiones.
- **Pedidos falsificados (CSRF)**: lo que modifica algo exige el encabezado
  `X-Pedido`, que un sitio ajeno no puede agregar.
- **En la copia pública** solo se abren `/auth/...` y `/api/yo/...` (con
  sesión); todo lo demás sigue siendo de solo lectura.
- **Dar de baja** a alguien (`desactivar`) cierra sus sesiones al instante.
- **Imágenes subidas**: el servidor las abre y las vuelve a codificar (WebP):
  lo que se guarda es siempre una imagen limpia, nunca el archivo tal cual llegó.
- **Backup**: las notas (y sus imágenes, que están en la base) son datos del grupo que no se pueden recuperar si se
  rompe el disco. Conviene la copia diaria fuera de la PC (pendiente).

## Puesta en marcha (una sola vez)

### 1. Credenciales de Google (gratis)

1. Entrar a https://console.cloud.google.com con una cuenta de Google del
   grupo y crear un proyecto (ej. "Pronostico UNLP").
2. **APIs y servicios → Pantalla de consentimiento de OAuth**: tipo
   *Externo*; nombre de la app ("Cartas Pronóstico UNLP"), mail de soporte.
   Permisos: solo `openid`, `email` y `profile` (son básicos: no hace falta
   que Google revise la app). Publicarla ("En producción"); si se deja en
   "Prueba", hay que agregar cada mail como usuario de prueba.
3. **Credenciales → Crear credenciales → ID de cliente de OAuth** → tipo
   *Aplicación web*. En **URI de redireccionamiento autorizados** poner,
   exacto:

       https://tifon-to-be-filled-by-o-e-m.tail6408fd.ts.net/auth/google/volver

4. Copiar el **ID de cliente** y el **secreto** al `.env` del servidor:

       GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
       GOOGLE_CLIENT_SECRET=xxxxxxxx
       URL_PUBLICA=https://tifon-to-be-filled-by-o-e-m.tail6408fd.ts.net

   (El secreto no se comparte ni se sube a GitHub: `.env` no está en el repo.)

5. `docker compose up -d` (para que la web lea el `.env`).

### 2. Dar de alta a los pronosticadores

```bash
docker compose exec web python -m meteo.web.cuentas agregar --email ana@gmail.com --nombre "Ana" --firma "Ana"
docker compose exec web python -m meteo.web.cuentas agregar --email coord@gmail.com --nombre "Coordi" --rol coordinador --firma "Coordi"
docker compose exec web python -m meteo.web.cuentas listar
docker compose exec web python -m meteo.web.cuentas desactivar --email ana@gmail.com
```

`--firma` es cómo firma los pronósticos en el portal (el campo "publicado
por": "Mate", "Micaela", "Leo"...). Se puede repetir si firmó de varias
formas. Sin firma, el perfil anda pero "Mis pronósticos" queda vacío.
Roles: `pronosticador` (por defecto), `coordinador` (ve las métricas de
todos y marca preguntas tratadas) y `admin` (además puede borrar cualquier nota).

## Pendiente

- Adjuntos en las notas: PDF de bibliografía y videos como links (las imágenes ya se pueden pegar).
