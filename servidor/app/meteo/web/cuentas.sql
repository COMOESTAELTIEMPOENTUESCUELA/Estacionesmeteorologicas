-- Cuentas de los pronosticadores (ingreso con Google), sus sesiones y su
-- libreta de notas. La web las crea sola al arrancar (IF NOT EXISTS).
-- Ver web/cuentas.py y docs/07-cuentas.md.

-- Quién puede entrar: SOLO los mails cargados acá (con el comando
-- "python -m meteo.web.cuentas agregar ..."). Google dice quién es; esta
-- tabla, si puede pasar.
CREATE TABLE IF NOT EXISTS usuario (
    id             serial PRIMARY KEY,
    email          text NOT NULL UNIQUE CHECK (email = lower(email)),
    nombre         text NOT NULL,
    rol            text NOT NULL DEFAULT 'pronosticador'
                   CHECK (rol IN ('pronosticador', 'coordinador', 'admin')),
    firmas         text[] NOT NULL DEFAULT '{}',   -- cómo firma los pronósticos en el portal ("Mate", "Micaela"...)
    activo         boolean NOT NULL DEFAULT true,
    creado         timestamptz NOT NULL DEFAULT now(),
    ultimo_ingreso timestamptz
);

-- Sesiones abiertas. Se guarda la HUELLA (sha256) del token, no el token: si
-- alguien leyera la base, no podría usar las sesiones.
CREATE TABLE IF NOT EXISTS sesion (
    huella     text PRIMARY KEY,
    usuario_id int NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    creada     timestamptz NOT NULL DEFAULT now(),
    expira     timestamptz NOT NULL,
    ip         text,
    agente     text
);
CREATE INDEX IF NOT EXISTS sesion_usuario ON sesion (usuario_id);

-- Libreta: lo que cada uno vio, le llamó la atención, sus dudas y las
-- preguntas para la reunión sinóptica. Con el contexto de dónde lo vio (qué
-- carta, qué corrida y plazo del GFS, qué ciudad), para volver a abrirlo.
CREATE TABLE IF NOT EXISTS nota (
    id          serial PRIMARY KEY,
    usuario_id  int NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    creada      timestamptz NOT NULL DEFAULT now(),
    actualizada timestamptz NOT NULL DEFAULT now(),
    tipo        text NOT NULL DEFAULT 'vi'
                CHECK (tipo IN ('vi', 'llamo_atencion', 'duda', 'pregunta')),
    texto       text NOT NULL CHECK (length(texto) BETWEEN 1 AND 10000),
    etiquetas   text[] NOT NULL DEFAULT '{}',
    compartida  boolean NOT NULL DEFAULT false,        -- false: solo la ve quien la escribió
    fecha_evento date,                                 -- el día que se analiza
    contexto    jsonb NOT NULL DEFAULT '{}',
    -- Preguntas para la reunión: se marcan tratadas y se anota la conclusión.
    tratada     boolean NOT NULL DEFAULT false,
    respuesta   text,
    tratada_por int REFERENCES usuario(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS nota_usuario ON nota (usuario_id, creada);
CREATE INDEX IF NOT EXISTS nota_fecha ON nota (fecha_evento);

-- Comentarios en las notas compartidas (el "muro del grupo").
CREATE TABLE IF NOT EXISTS nota_comentario (
    id         serial PRIMARY KEY,
    nota_id    int NOT NULL REFERENCES nota(id) ON DELETE CASCADE,
    usuario_id int NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    creada     timestamptz NOT NULL DEFAULT now(),
    texto      text NOT NULL CHECK (length(texto) BETWEEN 1 AND 2000)
);
CREATE INDEX IF NOT EXISTS nota_comentario_nota ON nota_comentario (nota_id, creada);

-- La "pizarra": una imagen pegada a la nota (la carta con lo que se dibujó,
-- el meteograma con lo que se marcó). Va en la base (y no en una carpeta
-- pública) para que la vean solo quienes pueden ver la nota, y para que el
-- backup de la base la incluya. El servidor la vuelve a codificar (WebP).
CREATE TABLE IF NOT EXISTS nota_imagen (
    nota_id  int PRIMARY KEY REFERENCES nota(id) ON DELETE CASCADE,
    datos    bytea NOT NULL,
    ancho    int NOT NULL,
    alto     int NOT NULL,
    creada   timestamptz NOT NULL DEFAULT now()
);

-- La parte "red social" del perfil: una presentación de cada uno, si muestra
-- sus números al grupo, reacciones y comentarios en las notas y en los
-- pronósticos publicados.
ALTER TABLE usuario ADD COLUMN IF NOT EXISTS bio text;
-- false: sus métricas (errores medios, aciertos) las ven solo él y los
-- coordinadores. Los pronósticos día por día ya son públicos (Verificación).
ALTER TABLE usuario ADD COLUMN IF NOT EXISTS metricas_publicas boolean NOT NULL DEFAULT false;

-- objeto: "nota:12" o "pron|La Plata|2026-11-02|Mate" (ciudad, fecha del
-- pronóstico y firma: una publicación).
CREATE TABLE IF NOT EXISTS reaccion (
    objeto     text NOT NULL,
    usuario_id int  NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    tipo       text NOT NULL CHECK (tipo IN ('util', 'acuerdo', 'duda', 'aplauso')),
    creada     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (objeto, usuario_id, tipo)
);
CREATE TABLE IF NOT EXISTS comentario_pronostico (
    id         serial PRIMARY KEY,
    objeto     text NOT NULL,
    usuario_id int  NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    creada     timestamptz NOT NULL DEFAULT now(),
    texto      text NOT NULL CHECK (length(texto) BETWEEN 1 AND 2000)
);
CREATE INDEX IF NOT EXISTS comentario_pronostico_objeto ON comentario_pronostico (objeto, creada);
