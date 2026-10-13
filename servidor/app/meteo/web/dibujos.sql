-- Dibujos de análisis sobre las cartas (frentes, centros, notas): uno por
-- carta y hora. La web lo crea sola al arrancar si no existe (CREATE ...
-- IF NOT EXISTS): no hace falta correrlo a mano.
CREATE TABLE IF NOT EXISTS carta_dibujo (
    producto    text        NOT NULL,       -- id de la carta (config/cartas.yaml)
    ts          timestamptz NOT NULL,       -- hora de la carta (UTC)
    dibujo      jsonb       NOT NULL,       -- {"ancho", "alto", "elementos": [...]}
    autor       text,
    actualizado timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (producto, ts)
);
