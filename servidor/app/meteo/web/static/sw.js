// Service worker mínimo: hace falta para que el celular deje "instalar" la
// página y la muestre en el menú Compartir (share_target del manifest).
// No guarda nada en caché: siempre se pide todo al servidor, así nunca se
// ve una versión vieja de la página.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
