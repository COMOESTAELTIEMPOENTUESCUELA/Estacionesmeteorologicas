"""
Arma static/placa.html copiando el DISEÑO de la placa (CSS y HTML) del
formulario de Pronóstico-UNLP (admin.html), para que Meteo UNLP dibuje
las placas "tal cual" las arma esa página.

Si en Pronóstico-UNLP cambian el diseño de la placa, se vuelve a correr:

    python servidor/scripts/copiar_placa.py ../Pronostico-unlp/admin.html

Qué copia y qué no:
- El CSS de la placa (desde ".placa-preview" hasta la regla de "img").
- El HTML de las dos placas (los dos <div class="placa-preview">), sin los
  "contenteditable" (acá no se edita nada) y con las imágenes apuntando a
  /placa/ (los logos con nombres sin espacios).
- La lógica que LLENA la placa no se copia: está escrita aparte, abajo
  (LOGICA), porque allá lee los casilleros del formulario y acá lee el JSON
  publicado. Imita las mismas reglas (textos, íconos, achicado de letra).
"""
import re
import sys
from pathlib import Path

DESTINO = Path(__file__).resolve().parents[1] / "app/meteo/web/static/placa.html"
LOGICA = Path(__file__).resolve().parent / "placa_logica.js"


def extraer(admin):
    lineas = admin.splitlines()
    i_css = next(i for i, l in enumerate(lineas) if l.strip().startswith(".placa-preview {"))
    f_css = next(i for i, l in enumerate(lineas) if l.strip().startswith("img {") and i > i_css)
    css = "\n".join(lineas[i_css:f_css + 2])        # + la regla de imágenes chicas que sigue
    i_html = next(i for i, l in enumerate(lineas) if '<div class="placa-preview">' in l)
    f_html = next(i for i, l in enumerate(lineas) if 'id="notification-toast"' in l)
    html = "\n".join(lineas[i_html:f_html])
    html = html[:html.rstrip().rfind("</div>")]       # el </div> que cierra #viewer
    html = re.sub(r'\s+contenteditable="true"\s+oninput="[^"]*"\s+onblur="[^"]*"\s+onkeydown="[^"]*"', "", html)
    html = (html.replace('src="images/Logo astronomicas y geofisicas.png"', 'src="/placa/logo_fcag.png"')
                .replace('src="images/Logo pronounlp.jfif"', 'src="/placa/logo_prono.jpg"')
                .replace('src="images/Logo unlp.png"', 'src="/placa/logo_unlp.png"')
                .replace('src="images/', 'src="/placa/'))
    raiz = re.search(r":root \{.*?\}", admin, re.S).group(0)
    return raiz, css, html


PAGINA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Placa del pronóstico · Meteo UNLP</title>
<!-- GENERADO por servidor/scripts/copiar_placa.py: no editar a mano el CSS/HTML de la placa. -->
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;700;800;900&display=swap" rel="stylesheet">
<!-- html2canvas 1.4.1 (MIT), la misma versión que usa el formulario, servida desde acá -->
<script src="/placa/html2canvas.min.js"></script>
<style>
* { box-sizing: border-box; }
@@RAIZ@@
body { font-family: 'Montserrat', sans-serif; margin: 0; background: #2d2d2d; color: #333; -webkit-font-smoothing: antialiased; }
#barra { position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: center;
  padding: 10px 12px; background: rgba(20,20,20,.92); backdrop-filter: blur(8px); color: #eee; font-size: 14px; }
#barra button { font: inherit; font-weight: 700; border: 0; border-radius: 8px; padding: 8px 14px; cursor: pointer; color: #fff; background: #003f6b; }
#barra button.sec { background: #45505a; }
#barra button:disabled { opacity: .5; cursor: wait; }
#estado { width: 100%; text-align: center; opacity: .8; font-size: 13px; }
#viewer { padding: 16px 0 40px; display: flex; flex-direction: column; align-items: center; gap: 40px; }
#aviso { color: #eee; text-align: center; padding: 60px 20px; font-size: 16px; }
@@CSS@@
</style>
</head>
<body>
<div id="barra">
  <button type="button" data-bajar="placa-1">⬇ Placa 1 (días 1-2)</button>
  <button type="button" data-bajar="placa-2">⬇ Placa 2 (días 3-4)</button>
  <span id="estado"></span>
</div>
<div id="viewer" hidden>
@@HTML@@
</div>
<div id="aviso">Cargando el pronóstico…</div>
<script>
@@LOGICA@@
</script>
</body>
</html>
"""


def main():
    origen = Path(sys.argv[1] if len(sys.argv) > 1 else "../Pronostico-unlp/admin.html")
    raiz, css, html = extraer(origen.read_text(encoding="utf-8"))
    pagina = (PAGINA.replace("@@RAIZ@@", raiz).replace("@@CSS@@", css)
              .replace("@@HTML@@", html).replace("@@LOGICA@@", LOGICA.read_text(encoding="utf-8")))
    DESTINO.write_text(pagina, encoding="utf-8")
    print(f"Escrito {DESTINO} ({len(pagina) // 1024} KB)")


if __name__ == "__main__":
    main()
