# 7. Cuentas de los pronosticadores (perfil, métricas y libreta)

La página de Cartas (también la copia pública) sigue igual para todos. Quien
**ingresa con Google** (y está en la lista del grupo) ve además:

- **Mi perfil → Mis pronósticos**: sus pronósticos contra lo observado y sus
  errores por plazo (MAE y sesgo de mínima y máxima, aciertos de lluvia y de
  dirección del viento), con las mismas cuentas que la pestaña Verificación.
  Se cuenta la **versión vigente** de cada pronóstico: si otro lo corrigió,
  ese día es de quien lo corrigió. Los coordinadores pueden ver los de cada uno.
- **Libreta**: notas de "lo que vi", "me llamó la atención", dudas y
  preguntas para la reunión, con etiquetas. Se escriben con los botones
  **📝 Anotar** de Cartas (cartas de análisis, visor grande, pronóstico GFS,
  diagnóstico por ciudad) y guardan **dónde** se escribieron: "Abrir" vuelve
  a esa carta, corrida y plazo, o ciudad. Privadas por defecto (las ven su
  autor y los administradores; la página lo avisa); las compartidas, todos
  los que ingresaron.
- **Coordinadores y administradores**: la tabla **"Todo el grupo"** (una
  fila por firma, aunque esa persona no tenga usuario: MAE de mínima y
  máxima del mismo día y del día siguiente, aciertos de lluvia) con "Ver
  detalle", el selector "De:" (por usuario o por firma) y el filtro de
  autor en la libreta. Los **administradores** además pueden ver las notas
  privadas de todos ("Incluir las privadas de todos").
- **Reunión sinóptica**: las preguntas compartidas, pendientes arriba. En la
  reunión se marcan tratadas y se anota la conclusión (quien la escribió o
  un coordinador).

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
- **Backup**: las notas son datos del grupo que no se pueden recuperar si se
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

- Adjuntos en las notas (imágenes, PDF de bibliografía; videos como links).
