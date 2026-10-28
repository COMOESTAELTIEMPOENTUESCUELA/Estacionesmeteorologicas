// Google Apps Script vinculado a la Google Sheet de reportes.
// Recibe los envíos del formulario público de ciencia-ciudadana.html y los agrega
// como fila nueva en la pestaña "Pendientes" (separada de "Reportes", que es la
// que lee el sitio).
//
// REVISIÓN SIN COPIAR Y PEGAR: cada fila de "Pendientes" tiene una columna
// "Estado" (Pendiente / Aprobado / Dudoso / Rechazado) que los coordinadores
// cambian con un menú. La pestaña "Reportes" se arma sola con una fórmula:
// los reportes publicados antes (pestaña "Historico") + los "Aprobado" de
// "Pendientes". Al cambiar el Estado, se anota quién lo revisó y cuándo.
//
// OJO: el sitio y el servidor leen la pestaña por su IDENTIFICADOR (gid=0),
// no por su nombre (en esta planilla se llama "Se muestra en página"). El
// script la busca por ese identificador.
//
// Instalación (o actualización):
// 1. Abrí tu Google Sheet → Extensiones → Apps Script.
// 2. Reemplazá el contenido de Code.gs por este archivo completo y guardá.
// 3. La PRIMERA vez con la revisión: elegí la función "configurarRevision" en el
//    menú de arriba y tocá "Ejecutar" (una sola vez). Ver qué hace más abajo.
// 4. Si es la primera instalación: Implementar → Nueva implementación → tipo
//    "Aplicación web" → Ejecutar como "Yo" → Quién tiene acceso "Cualquier
//    usuario" → Implementar, y copiá la URL /exec en APPS_SCRIPT_URL de
//    ciencia-ciudadana.html. Si ya estaba implementado: Implementar →
//    Administrar implementaciones → editar (lápiz) → Versión "Nueva versión" →
//    Implementar (así la URL no cambia).

var PENDIENTES_SHEET_NAME = 'Pendientes';
var GID_SITIO = 0;                           // la pestaña que lee el sitio (por identificador, no por nombre)
var HISTORICO_SHEET_NAME = 'Historico';      // lo publicado a mano antes de la revisión con Estado
var FOTOS_FOLDER_NAME = 'Reportes Comunidad - Fotos';
var ESTADOS = ['Pendiente', 'Aprobado', 'Dudoso', 'Rechazado'];
// Instrumento y Periodo van después de las 10 columnas que ciencia-ciudadana.html
// lee por posición; Estado y quién revisó, al final de todo.
var ENCABEZADOS = ['Fecha', 'Tipo', 'Escuela', 'Reportero', 'Lluvia_mm', 'Estado_Camino', 'Resumen', 'Imagen_URL',
                   'Lat', 'Lon', 'Instrumento', 'Periodo', 'Estado', 'Revisado_por', 'Revisado_el'];
var COL_ESTADO = 13;            // columna M

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var sheet = hojaPendientes_();

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
      data.instrument || '',
      data.period || '',
      'Pendiente',
      '',
      '',
    ]);

    return ContentService.createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// La pestaña "Pendientes", con todos los encabezados (agrega al final los que
// falten si la planilla es de una versión anterior, sin tocar las columnas de antes).
function hojaPendientes_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(PENDIENTES_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(PENDIENTES_SHEET_NAME);
    sheet.appendRow(ENCABEZADOS);
  } else if (sheet.getLastColumn() < ENCABEZADOS.length) {
    var faltan = ENCABEZADOS.slice(sheet.getLastColumn());
    sheet.getRange(1, sheet.getLastColumn() + 1, 1, faltan.length).setValues([faltan]);
  }
  return sheet;
}

// Se ejecuta UNA vez (a mano, desde el editor) para pasar a la revisión con Estado:
// 1. Agrega las columnas Estado, Revisado_por y Revisado_el a "Pendientes", con
//    un menú desplegable y colores. Las filas que ya estaban quedan como
//    "Revisado antes" (ya se publicaron a mano o se descartaron).
// 2. Copia lo que hoy tiene "Reportes" a una pestaña nueva "Historico" (solo
//    valores) y en "Reportes" deja una fórmula: Historico + los "Aprobado".
//    "Reportes" conserva su identificador (gid 0): el sitio la sigue leyendo igual.
// 3. Instala el disparador que anota quién revisó y cuándo.
function configurarRevision() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var pend = hojaPendientes_();
  var n = pend.getLastRow() - 1;

  // 1) Estado con menú y colores
  if (n > 0) {
    var estados = pend.getRange(2, COL_ESTADO, n, 1).getValues();
    for (var i = 0; i < n; i++) if (!estados[i][0]) estados[i][0] = 'Revisado antes';
    pend.getRange(2, COL_ESTADO, n, 1).setValues(estados);
  }
  var regla = SpreadsheetApp.newDataValidation().requireValueInList(ESTADOS.concat(['Revisado antes']), true)
    .setAllowInvalid(false).build();
  pend.getRange(2, COL_ESTADO, pend.getMaxRows() - 1, 1).setDataValidation(regla);
  var colEstado = pend.getRange(2, COL_ESTADO, pend.getMaxRows() - 1, 1);
  var colores = { 'Pendiente': '#fff2cc', 'Aprobado': '#d9ead3', 'Dudoso': '#fce5cd', 'Rechazado': '#f4cccc' };
  var reglas = pend.getConditionalFormatRules().filter(function(r) {
    return r.getRanges()[0].getColumn() !== COL_ESTADO;
  });
  Object.keys(colores).forEach(function(est) {
    reglas.push(SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo(est)
      .setBackground(colores[est]).setRanges([colEstado]).build());
  });
  pend.setConditionalFormatRules(reglas);
  pend.setFrozenRows(1);

  // 2) Historico + fórmula en la pestaña que lee el sitio
  var sitio = hojaDelSitio_();
  if (sitio && !ss.getSheetByName(HISTORICO_SHEET_NAME)) {
    var hist = ss.insertSheet(HISTORICO_SHEET_NAME);
    copiarValores_(sitio, hist);
    sitio.clear();
    ponerFormula_(sitio);
  }

  // 3) Disparador: anota quién revisó y cuándo (uno solo, aunque se ejecute de nuevo)
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'alEditar') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('alEditar').forSpreadsheet(ss).onEdit().create();
}

// La pestaña que leen el sitio y el servidor (gid=0), se llame como se llame.
function hojaDelSitio_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets().filter(function(h) {
    return h.getSheetId() === GID_SITIO;
  })[0];
}

function copiarValores_(origen, destino) {
  var valores = origen.getDataRange().getValues();
  destino.clear();
  destino.getRange(1, 1, valores.length, valores[0].length).setValues(valores);
  // Que tenga las 12 columnas publicadas (si el origen tenía menos, quedan vacías).
  if (destino.getLastColumn() < 12) {
    destino.getRange(1, 1, 1, 12).setValues([ENCABEZADOS.slice(0, 12)]);
  }
}

// Historico (sin filas vacías) y debajo los "Aprobado". Si todavía no hay
// ninguno aprobado, el FILTER da error y queda solo el Historico.
function ponerFormula_(hoja) {
  var historico = 'FILTER(' + HISTORICO_SHEET_NAME + '!A1:L, ' + HISTORICO_SHEET_NAME + '!A1:A<>"")';
  var aprobados = 'FILTER(' + PENDIENTES_SHEET_NAME + '!A2:L, ' + PENDIENTES_SHEET_NAME + '!M2:M="Aprobado")';
  hoja.getRange('A1').setFormula('=IFERROR({' + historico + '; ' + aprobados + '}, ' + historico + ')');
}

// ARREGLO (una sola vez, 28/10/2026): la primera versión de configurarRevision
// buscaba la pestaña "Reportes" por nombre, pero el sitio lee la de gid=0
// ("Se muestra en página"). Esto deja todo como tenía que quedar:
// 1. "Reportes" recupera su contenido original (que había quedado en "Historico").
// 2. "Historico" pasa a tener lo que hoy muestra el sitio.
// 3. La pestaña del sitio queda con la fórmula (Historico + Aprobado).
function corregirHojaDelSitio() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sitio = hojaDelSitio_();
  var rep = ss.getSheetByName('Reportes');
  var hist = ss.getSheetByName(HISTORICO_SHEET_NAME);
  if (!sitio || !hist) throw new Error('No encuentro la pestaña del sitio (gid 0) o "Historico".');
  if (String(sitio.getRange('A1').getFormula()).indexOf('Historico') !== -1) {
    throw new Error('La pestaña del sitio ya tiene la fórmula: no hay nada que corregir.');
  }
  if (rep && rep.getSheetId() !== GID_SITIO) {
    rep.clear();
    copiarValores_(hist, rep);          // 1) Reportes vuelve a como estaba
  }
  copiarValores_(sitio, hist);          // 2) Historico = lo que muestra el sitio
  sitio.clear();
  ponerFormula_(sitio);                 // 3) la fórmula, en la pestaña del sitio
}

// Al cambiar el Estado de un reporte: quién lo cambió y cuándo.
// (Con cuentas de Gmail comunes, Google a veces no informa el mail de otros
// editores: en ese caso queda solo la fecha.)
function alEditar(e) {
  var hoja = e.range.getSheet();
  if (hoja.getName() !== PENDIENTES_SHEET_NAME || e.range.getColumn() !== COL_ESTADO || e.range.getRow() < 2) return;
  var quien = (e.user && e.user.getEmail && e.user.getEmail()) || Session.getActiveUser().getEmail() || '';
  hoja.getRange(e.range.getRow(), COL_ESTADO + 1, e.range.getNumRows(), 2)
    .setValues(Array(e.range.getNumRows()).fill([quien, new Date()]));
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

  return 'https://lh3.googleusercontent.com/d/' + file.getId();
}

function obtenerOCrearCarpeta(nombre) {
  var carpetas = DriveApp.getFoldersByName(nombre);
  if (carpetas.hasNext()) return carpetas.next();
  var nueva = DriveApp.createFolder(nombre);
  nueva.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  return nueva;
}
