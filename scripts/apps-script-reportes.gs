// Google Apps Script vinculado a la Google Sheet de reportes.
// Recibe los envíos del formulario público de ciencia-ciudadana.html y los agrega
// como fila nueva en la pestaña "Pendientes" (separada de "Reportes", que es la
// que lee el sitio). Un coordinador revisa "Pendientes" y copia a "Reportes" lo
// que quiera publicar.
//
// Instalación:
// 1. Abrí tu Google Sheet → Extensiones → Apps Script.
// 2. Borrá el contenido de Code.gs y pegá este archivo completo.
// 3. Reemplazá SHEET_ID si hace falta (por defecto usa la planilla activa, así que no es necesario).
// 4. Implementar → Nueva implementación → tipo "Aplicación web" → Ejecutar como "Yo" →
//    Quién tiene acceso "Cualquier usuario" → Implementar.
// 5. Autorizá los permisos que pida Google (es tu propio script, sobre tu propia planilla).
// 6. Copiá la URL que termina en /exec y pegala en APPS_SCRIPT_URL, en ciencia-ciudadana.html.

var PENDIENTES_SHEET_NAME = 'Pendientes';
var FOTOS_FOLDER_NAME = 'Reportes Comunidad - Fotos';

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(PENDIENTES_SHEET_NAME);
    if (!sheet) {
      sheet = ss.insertSheet(PENDIENTES_SHEET_NAME);
      sheet.appendRow(['Fecha', 'Tipo', 'Escuela', 'Reportero', 'Lluvia_mm', 'Estado_Camino', 'Resumen', 'Imagen_URL', 'Lat', 'Lon']);
    }

    var imageUrl = '';
    if (data.imageBase64 && data.imageName) {
      imageUrl = subirFoto(data.imageBase64, data.imageName);
    }

    sheet.appendRow([
      new Date(),
      data.type || 'otro',
      data.school || '',
      data.reporter || '',
      data.rain_mm || '',
      data.road_status || '',
      data.summary || '',
      imageUrl,
      data.lat || '',
      data.lon || '',
    ]);

    return ContentService.createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function subirFoto(base64Data, fileName) {
  var partes = base64Data.split(',');
  var contenido = partes.length > 1 ? partes[1] : partes[0];
  var mimeMatch = base64Data.match(/^data:([^;]+);/);
  var mimeType = mimeMatch ? mimeMatch[1] : 'image/jpeg';

  var bytes = Utilities.base64Decode(contenido);
  var blob = Utilities.newBlob(bytes, mimeType, fileName);

  var folder = obtenerOCrearCarpeta(FOTOS_FOLDER_NAME);
  var file = folder.createFile(blob);
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);

  return 'https://drive.google.com/uc?export=view&id=' + file.getId();
}

function obtenerOCrearCarpeta(nombre) {
  var carpetas = DriveApp.getFoldersByName(nombre);
  if (carpetas.hasNext()) return carpetas.next();
  var nueva = DriveApp.createFolder(nombre);
  nueva.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  return nueva;
}
