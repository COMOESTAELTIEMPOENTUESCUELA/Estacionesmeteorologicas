// Service worker mínimo: hace falta para que el celular deje "instalar" la
// página y la muestre en el menú Compartir (share_target del manifest), y para
// recibir los avisos (Web Push) aunque la página esté cerrada.
// No guarda nada en caché: siempre se pide todo al servidor, así nunca se
// ve una versión vieja de la página.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

// Llega un aviso del servidor (cifrado; el navegador ya lo descifró).
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { texto: e.data ? e.data.text() : "" }; }
  const mostrar = self.registration.showNotification(d.titulo || "Meteo UNLP", {
    body: d.texto || "Tenés un aviso nuevo",
    icon: "/iconos/icono-192.png",
    badge: "/iconos/icono-192.png",
    tag: d.etiqueta || "meteo",
    data: { url: d.url || "/#grupo" },
  });
  // El numerito sobre el ícono (donde el sistema lo permite).
  const numero = "setAppBadge" in navigator && d.no_leidas ? navigator.setAppBadge(d.no_leidas).catch(() => {}) : null;
  e.waitUntil(Promise.all([mostrar, numero]));
});

// Al tocar el aviso: si la página ya está abierta, se la trae al frente y se
// la lleva al aviso; si no, se la abre.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || "/", self.location.origin).href;
  e.waitUntil((async () => {
    const abiertas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of abiertas) {
      if (new URL(c.url).origin === self.location.origin) {
        await c.focus();
        c.postMessage({ aviso: url });
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
