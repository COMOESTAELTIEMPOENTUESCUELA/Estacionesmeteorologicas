# 7. Cuentas de los pronosticadores (perfil, métricas y libreta)

La página de Cartas (también la copia pública) sigue igual para todos. Quien
**ingresa con Google** (y está en la lista del grupo) ve además:

- **Mi perfil → Mis pronósticos**: sus pronósticos contra lo observado y sus
  errores por plazo (MAE y sesgo de mínima y máxima, aciertos de lluvia y de
  dirección del viento), con las mismas cuentas que la pestaña Verificación.
  Se cuenta la **versión vigente** de cada pronóstico: si otro lo corrigió,
  ese día es de quien lo corrigió. Los coordinadores pueden ver los de cada uno.
- **Las secciones de la página** (arriba), según lo que se hace en un turno:
  **Ahora** (Mapa, EMAs, Estaciones SMN, Lluvia diaria, Comparar, Estado y
  fallas: en la sub-barra), **Análisis** (cartas de análisis, satélite,
  observaciones de la hora, meteogramas, corte y sondeos), **Pronóstico**
  (Modelo GFS y Lo que pronosticamos), **Verificación** y **Grupo** (la red,
  con sesión). La dirección lo refleja (`#analisis`, `#pronostico`,
  `#verificacion`, `#grupo`, `#smn`...) y los enlaces viejos (`#perfil`,
  `#cartas`) siguen llevando al lugar nuevo.
- **Grupo (antes "Mi perfil") es una red del grupo**, con un menú a la izquierda:
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
  - **Panorama de un día**: el gráfico de cada pronóstico tiene los valores
    escritos ("26 → **27,2**" = pronosticado → observado). Tocando un día del
    gráfico se abre el panorama de ESA fecha en esa ciudad: lo observado, un
    gráfico de cómo se fue acercando el pronóstico (lo que pronosticó cada
    uno con 3, 2, 1 días de anticipación y el mismo día, y el GFS de ese
    momento) contra lo observado, la tabla con todos los pronósticos y sus
    errores, botones a las cartas del día y a las del día en que se
    pronosticó (lo que se veía), y las notas con esa fecha. ‹ › pasan de
    día. Sirve para ver si un error fue de todos (situación difícil) o de uno.
    (`GET /api/yo/dia?ciudad=&fecha=`.)
  - **Notas para un día que viene**: al publicar, "📅 Hoy / Mañana / Pasado
    mañana / En 3 o 4 días". La nota queda con esa fecha (aparece en el
    panorama de ese día) y con la corrida del GFS de ese momento a las 15 hs
    de ese día: "🗺️ Cartas del pronóstico" las abre, y la nota queda
    flotando encima de las cartas (✕ la oculta; "← Volver a la nota" vuelve).
    Al abrir cualquier nota desde su 📍 pasa lo mismo.
  - **Foto de perfil**: en Mi perfil, "📷 Subir foto". Se achica en el
    navegador, el servidor la recorta cuadrada (320 px, WebP, sin los datos
    del archivo como la ubicación GPS) y aparece en lugar del círculo con la
    inicial en todos lados. Solo la ven los que ingresaron. "Quitar foto"
    vuelve a la inicial.
  - **👁 Ver como pronosticador** (administradores y coordinadores, arriba a
    la derecha): la página se ve con los permisos de un pronosticador común
    (el servidor se los baja de verdad, no es una imitación). Una franja
    amarilla lo recuerda; "Volver a mi vista" lo apaga (si no, se apaga solo
    al día siguiente).
  - **💬 Mensajes** (en el menú y en la barra de abajo del celular, con el
    número de no leídos): chats **privados** (de a dos) y **grupales** (con
    nombre). Se puede adjuntar un **pronóstico publicado** (🌡️ en el chat, o
    "💬 Enviar" en la tarjeta del pronóstico): se ve con su gráfico. Cada uno
    puede pasar un mensaje **propio** al Inicio con "📢 Compartir con el
    grupo" (queda como nota, con el pronóstico adjunto); los de otros no, para
    no publicar lo que alguien escribió en privado. **💡 Dudas y
    sugerencias** abre una conversación con los administradores. Los
    mensajes nuevos llegan solos cada 5 s con la conversación abierta. Los
    administradores pueden ver la base de datos (y con eso, los mensajes): la
    página lo avisa.
  - **🛠️ Administración** (solo rol admin), en el menú de la izquierda:
    - **Verificación**: los parámetros con que se calcula lo observado
      (umbral de lluvia, completitud del día y del viento, superposición de
      la lluvia de 24 h, topes de las categorías de viento) se cambian ahí:
      solo números entre su mínimo y su máximo (el servidor los revisa), con
      quién y cuándo los cambió. Se guardan en la tabla `ajuste` y se usan
      desde el próximo cálculo; "Recalcular" los aplica a los últimos días.
      También se ven las estaciones de cada ciudad y el código de la
      verificación, **solo para leer**: el código no se cambia desde la
      página (sería dejar que alguien ejecute cualquier cosa en el
      servidor); se cambia en el repositorio, con sus tests.
    - **Base de datos**: todas las tablas, de a 100 filas, ordenando por
      cualquier columna y buscando texto. **Solo lectura** (la consulta va
      por la conexión que la base tiene en modo solo lectura) y sin las
      huellas de contraseñas y sesiones.
  - **Letra  A−  100 %  A+** (arriba, junto al tema): achica o agranda la
    letra y toda la página (100, 115, 130 o 150 %); tocando el porcentaje
    vuelve a 100 %. Con zoom de CSS; queda guardado en ese navegador.
  - **Modo Análisis 🤓** (interruptor arriba): sin él, la página muestra lo
    esencial (sin la tabla de cada pronóstico, el gráfico del modelo, los
    textos de ayuda fijos ni los atajos de teclado). Se recuerda en cada
    navegador.
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
  - **Reunión sinóptica**: un tablero de tres columnas. **Propuestas**
    (cualquiera agrega), **En discusión** (lo que se está mirando en la
    reunión) y **Conclusión** (el consenso, que queda en el historial). Se
    mueven con botones o arrastrando (en la compu); las mueve quien la
    escribió o un coordinador.
  - **🖥️ Presentar** (coordinadores): lo que está en discusión (o cualquier
    nota, con 🖥️) a pantalla completa, sin menús, con ← → para pasar y los
    botones para concluir ahí mismo.
  - **Métricas del grupo** (coordinadores): la tabla de todas las firmas.
  - **🌆 Isla de calor (La Plata)**: el ΔT = temperatura del Observatorio
    (Campbell y Davis) − La Plata Aero, hora por hora, con pistas (cielo
    despejado, calma, brisa del río, noche de enfriamiento radiativo) y la
    curva de las últimas 36 h (Chart.js). Se actualiza sola cada 5 minutos.
    En la columna derecha (en pantallas chicas, arriba del Inicio).
  - **Verificación de doble filo**: los pronósticos muestran mínima y máxima
    **urbana** y **suburbana**, con 🏆 donde quien pronosticó estuvo más
    cerca que el GFS de ese momento (el modelo no distingue la ciudad del
    campo), y "le ganó al GFS en X de Y".
  - **📈 Ver el modelo en vivo**: además del GFS, lo pronosticado y las
    extremas, la temperatura **medida hora por hora** (SMN y, en La Plata, el
    Observatorio) se dibuja paso a paso y se completa sola mientras llegan
    los datos. **📝 Anotar como duda** lleva el gráfico a la pizarra.
  - **🤖 Bot observador** (`ingesta/bot.py`, cada 15 min): si la presión baja
    3 hPa o más en 3 h, la temperatura baja 4 °C o más en 1 h (sinópticas
    del SMN y el Observatorio), hay una ráfaga de 60 km/h o más o el SYNOP
    informa tormenta, publica una nota ⚡ con #alertanowcasting para que el
    grupo comente. Una sola nota por vuelta y como mucho una alerta por
    estación cada 3 h. El bot no puede ingresar a la página ni aparece en
    "El grupo".
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

**Con contraseña (sin Google).** Se puede entrar con el mail **o** un nombre
de usuario, y una contraseña. Guardamos solo su huella (scrypt), nunca la
contraseña; después de 5 intentos fallidos se frena 15 minutos.

```bash
# alta con nombre de usuario y contraseña temporal al azar (la muestra una sola vez)
docker compose exec web python -m meteo.web.cuentas agregar --email dylan@hotmail.com --nombre "Dylan" --firma "Dylan" --usuario meteodylan --con-clave
# o poner una contraseña elegida (la pide sin mostrarla; no queda en el historial)
docker compose exec web python -m meteo.web.cuentas clave --email dylan@hotmail.com --elegir
# si alguien se olvidó la suya: generarle otra temporal
docker compose exec web python -m meteo.web.cuentas clave --email dylan@hotmail.com
```

La que pone el administrador es **temporal**: al entrar, la página pide
elegir una propia (10 caracteres o más). Cada uno la cambia cuando quiera con 🔑.

`--firma` es cómo firma los pronósticos en el portal (el campo "publicado
por": "Mate", "Micaela", "Leo"...). Se puede repetir si firmó de varias
formas. Sin firma, el perfil anda pero "Mis pronósticos" queda vacío.
Roles: `pronosticador` (por defecto), `coordinador` (ve las métricas de
todos y marca preguntas tratadas) y `admin` (además puede borrar cualquier nota).

## Pendiente

- Adjuntos en las notas: PDF de bibliografía y videos como links (las imágenes ya se pueden pegar).
