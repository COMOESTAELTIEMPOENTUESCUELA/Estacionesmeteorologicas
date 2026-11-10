// Llena la placa (copiada del formulario de Pronóstico-UNLP) con el JSON
// publicado: /api/yo/pronostico/placa?objeto=pron|ciudad|fecha|firma.
// Imita las reglas del formulario (admin.html): updateSky, updateWind,
// updateWindLegendAndArrows, up, updateCity, recalculateDates,
// updateDayTitleSize, syncRowFontSize y fitDayDescription.
"use strict";
const $ = (id) => document.getElementById(id);
const DIAS = ["DOMINGO", "LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO"];
const MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"];
const ICONOS = {
  "despejado": { dia: "soleado.png", noche: "Despejado_noche.png" },
  "ligeramente nublado": { dia: "Ligeramente_nublado_dia.png", noche: "Ligeramente_nublado_noche.png" },
  "algo nublado": { dia: "Algo_nublado_dia.png", noche: "Algo_nublado_noche.png" },
  "parcialmente nublado": { dia: "Parcialmente_nublado_dia.png", noche: "Parcialmente_nublado_noche.png" },
  "cubierto": { dia: "Cubierto_dia.png", noche: "Cubierto_noche.png" },
  "neblinas": { dia: "Neblina.png", noche: "Neblina.png" },
  "nieblas": { dia: "Nieblas_dia.png", noche: "Nieblas_dia.png" },
  "lluvias": { dia: "Lluvia_dia.png", noche: "Lluvia_noche.png" },
  "lluvias aisladas": { dia: "Lluvia_dia.png", noche: "Lluvia_noche.png" },
  "lloviznas": { dia: "Lloviznas_dia.png", noche: "Lloviznas_noche.png" },
  "lluvias y tormentas": { dia: "Tormentas_dia.png", noche: "Tormentas_noche.png" },
  "lluvias y tormentas aisladas": { dia: "Tormentas_dia.png", noche: "Tormentas_noche.png" },
  "tormentas aisladas": { dia: "Tormentas_dia.png", noche: "Tormentas_noche.png" },
};
const ANGULOS = { norte: 180, noreste: 225, este: 270, sudeste: 315, sur: 0, sudoeste: 45, oeste: 90, noroeste: 135 };
// Día 1 (h) tiene 3 tramos, Día 2 (m) 4, Días 3 y 4 dos (madrugada/mañana y tarde/noche).
const CONF = [{ id: "h", n: 3 }, { id: "m", n: 4 }, { id: "d3", n: 2 }, { id: "d4", n: 2 }];

const may1 = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : "");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const conKmh = (r) => { r = r == null ? "" : String(r).trim(); return !r ? "" : /km\/h/i.test(r) ? r : `${r} km/h`; };
const momento = (dia, t) => dia === "h" ? (t === 3 ? "noche" : "dia") : dia === "m" ? (t === 1 || t === 4 ? "noche" : "dia") : (t === 2 ? "noche" : "dia");

// up(): temperatura con "°C" chiquito, y "*" en la suburbana.
function up(id, v) {
  const el = $(id); if (!el) return;
  const val = v === null || v === undefined || v === "" ? "--" : String(v).trim();
  const esTemp = /min|max/.test(id) || /-t[1-4]$/.test(id);
  if (esTemp && val !== "--") el.innerHTML = `${esc(val)}<span class="unit-c">°C</span>${id.includes("sub") ? "*" : ""}`;
  else el.innerText = val;
}

// Achica la letra de a 1 px hasta que el texto entre en su casillero.
function achicar(el, max, min) {
  if (!el) return;
  let t = max; el.style.fontSize = t + "px";
  while ((el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth) && t > min) el.style.fontSize = --t + "px";
}

function flecha(dir) {
  const a = ANGULOS[dir] ?? 0;
  return `<div style="width:72px;height:72px;margin:0 8px;flex-shrink:0"><svg viewBox="0 0 100 100" width="72" height="72" style="display:block"><g transform="rotate(${a} 50 50)"><path d="M 35 75 L 65 75 L 65 40 L 85 40 L 50 10 L 15 40 L 35 40 Z" fill="#ffffff"/></g></svg></div>`;
}

function icono(dia, t, p, cielo) {
  const img = $(`${dia}-icon-${t}`); if (!img) return;
  const calculado = ICONOS[cielo] ? ICONOS[cielo][momento(dia, t)] : "default.png";
  // El formulario guarda el nombre del ícono que se vio (con o sin viento): ese manda.
  const cod = p.weather_icon_code && /^[\w-]+$/.test(p.weather_icon_code) ? `${p.weather_icon_code}.png` : calculado;
  img.onerror = () => { img.onerror = null; img.src = `/placa/${calculado}`; };
  img.src = `/placa/${cod}`;
}

function vientoTarjeta(dia, p1, p2) {     // Días 3 y 4: flechas + una frase
  const i1 = p1.wind_intensity, d1 = p1.wind_direction, i2 = p2.wind_intensity, d2 = p2.wind_direction;
  const r1 = conKmh(p1.wind_gusts), r2 = conKmh(p2.wind_gusts);
  const tw = $(`${dia}-wind-text`), ac = $(`${dia}-arrows-container`);
  ac.innerHTML = (i1 && d1 ? flecha(d1) : "") + (i2 && d2 ? flecha(d2) : "");
  let base = "";
  if (i1 && d1 && i2 && d2) {
    if (i1 === i2 && d1 === d2) base = `Vientos ${i1} del ${d1}.`;
    else if (i1 === i2) base = `Vientos ${i1} del ${d1} rotando al ${d2}.`;
    else base = `Vientos ${i1} del ${d1} rotando a ${i2} del ${d2}.`;
  } else if (i1 && d1) base = `Vientos ${i1} del ${d1}.`;
  else if (i2 && d2) base = `Vientos ${i2} del ${d2}.`;
  const raf = [...new Set([r1, r2].filter(Boolean))];
  const r = raf.length === 1 ? ` <u>Ráfagas de ${esc(raf[0])}.</u>` : raf.length === 2 ? ` <u>Ráfagas de ${esc(raf[0])} y de ${esc(raf[1])}.</u>` : "";
  tw.innerHTML = (esc(base) + r).trim() || "--";
  const lc = $(`${dia}-wind-legend`);
  let t = 28; tw.style.fontSize = t + "px";
  while (lc.scrollHeight > lc.clientHeight && t > 14) tw.style.fontSize = --t + "px";
}

function descripcionTarjeta(dia, texto) {  // Días 3 y 4: la descripción extendida, sin pisar al viento
  const el = $(`${dia}-w1`), caja = $(`${dia}-desc-container`);
  el.innerText = texto && texto.trim() ? texto : "--";
  const viento = caja.closest(".v-card").querySelector(".wind-section");
  const hueco = viento.getBoundingClientRect().top - caja.getBoundingClientRect().top;
  caja.style.height = (hueco > 0 ? Math.min(140, Math.max(40, hueco - 10)) : 140) + "px";
  let t = 28; el.style.fontSize = t + "px";
  while (el.scrollHeight > caja.clientHeight && t > 12) el.style.fontSize = --t + "px";
}

function ciudad(nombre) {
  document.body.classList.toggle("single-station", nombre !== "La Plata");
  const N = nombre.toUpperCase();
  document.querySelectorAll(".city-label").forEach((el) => {
    el.innerText = N;
    const [t, l, m] = N === "MAR DEL PLATA" || N === "BAHÍA BLANCA" ? [54, -1.5, -20] : N === "JUNÍN" ? [72, -2, 50] : [72, -2, -10];
    el.style.fontSize = t + "px"; el.style.letterSpacing = l + "px"; el.style.marginLeft = m + "px";
  });
}

function llenar(p) {
  ciudad(p.city || "");
  const hm = String(p.emission_time || "").match(/(\d{1,2}):(\d{2})/);
  $("out-hora").innerText = hm ? `${hm[1].padStart(2, "0")}:${hm[2]} hs` : "--";
  const base = new Date(`${p.forecast_date}T12:00:00`);
  const fecha = (d) => `${String(d.getDate()).padStart(2, "0")}/${MESES[d.getMonth()]}/${String(d.getFullYear()).slice(-2)}`;
  const dias = p.daily_forecasts || [];
  CONF.forEach((c, i) => {
    const d = new Date(base); d.setDate(base.getDate() + i);
    const nombre = DIAS[d.getDay()];
    document.querySelectorAll(`.n${i}`).forEach((el) => {
      el.innerText = nombre;
      el.style.fontSize = (i >= 2 ? 72 : nombre === "MIÉRCOLES" ? 34 : 44) + "px";
    });
    document.querySelectorAll(`.f${i}`).forEach((el) => (el.innerText = fecha(d)));
    const dd = dias[i] || {};
    up(`${c.id}-min`, dd.temp_min); up(`${c.id}-max`, dd.temp_max);
    up(`${c.id}-min-sub`, dd.temp_min_suburban); up(`${c.id}-max-sub`, dd.temp_max_suburban);
    const per = dd.period_forecasts || [];
    for (let t = 1; t <= c.n; t++) {
      const q = per[t - 1] || {};
      const cielo = String(q.precipitation_description || q.sky_condition || "").toLowerCase();
      const pop = q.probability_of_precipitation || "";
      icono(c.id, t, q, cielo);
      if (c.id === "h" || c.id === "m") {
        up(`${c.id}-t${t}`, q.temperature);
        const w = $(`${c.id}-w${t}`);
        w.innerText = cielo ? (pop ? may1(`${pop} prob. de ${cielo}`) : may1(cielo)) : (pop ? `${pop} prob. precipitaciones` : "--");
        achicar(w, 34, 20);
        const r = conKmh(q.wind_gusts), v = $(`${c.id}-v${t}`);
        v.innerText = r ? `Ráfagas: ${r}` : (q.wind_intensity && q.wind_direction ? may1(`${q.wind_intensity} del ${q.wind_direction}`) : "--");
        achicar(v, 34, 20);
      }
    }
    if (c.id === "d3" || c.id === "d4") {
      vientoTarjeta(c.id, per[0] || {}, per[1] || {});
      descripcionTarjeta(c.id, dd.custom_description);
    }
  });
}

// La placa mide 1080 × 1350: en pantalla se ve achicada (zoom), pero se
// llena y se exporta a tamaño real, para que las medidas den igual que allá.
function ajustarVista() {
  const v = $("viewer");
  v.style.zoom = Math.min(1, (window.innerWidth - 24) / 1080);
}

async function bajar(id, boton) {
  const v = $("viewer"), antes = v.style.zoom, el = $(id);
  boton.disabled = true; $("estado").textContent = "Armando la imagen…";
  try {
    await document.fonts.ready;
    v.style.zoom = 1; el.classList.add("exporting");
    await new Promise((r) => setTimeout(r, 50));
    await Promise.all([...el.querySelectorAll("img")].map((i) => i.complete ? 0 : new Promise((r) => { i.onload = i.onerror = r; })));
    const lienzo = await html2canvas(el, { scale: 2, useCORS: true, logging: false, backgroundColor: null });
    const a = document.createElement("a");
    a.download = `Pronostico_${(window.PLACA?.city || "").replace(/\s+/g, "_")}_${window.PLACA?.forecast_date || ""}_${id === "placa-1" ? "dias1-2" : "dias3-4"}.png`;
    a.href = lienzo.toDataURL("image/png"); a.click();
    $("estado").textContent = "Imagen descargada.";
  } catch (e) {
    $("estado").textContent = "No se pudo armar la imagen: " + e.message;
  } finally {
    el.classList.remove("exporting"); v.style.zoom = antes; boton.disabled = false;
  }
}

async function iniciar() {
  const objeto = new URLSearchParams(location.search).get("objeto");
  const aviso = $("aviso");
  if (!objeto) { aviso.textContent = "Falta indicar qué pronóstico mostrar."; return; }
  let p;
  try {
    const r = await fetch(`/api/yo/pronostico/placa?objeto=${encodeURIComponent(objeto)}`, { credentials: "same-origin" });
    if (!r.ok) throw new Error(r.status === 401 ? "Hay que entrar con tu usuario." : r.status === 404 ? "Ese pronóstico ya no existe." : `Error ${r.status}`);
    p = await r.json();
  } catch (e) { aviso.textContent = e.message; return; }
  window.PLACA = p;
  await document.fonts.ready;
  $("viewer").hidden = false; aviso.hidden = true;
  llenar(p);
  if (!(p.daily_forecasts || []).some((d) => (d.period_forecasts || []).length))
    $("estado").textContent = "Esta publicación se guardó resumida (sin tramos): la placa sale solo con temperaturas.";
  ajustarVista();
  window.addEventListener("resize", ajustarVista);
  document.querySelectorAll("[data-bajar]").forEach((b) => b.addEventListener("click", () => bajar(b.dataset.bajar, b)));
}
iniciar();
