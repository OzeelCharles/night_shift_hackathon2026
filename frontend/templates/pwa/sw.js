/* Rivezli : service worker (version {{ version }}).
   Il ne met en cache que des fichiers statiques et la page hors ligne. Les pages de l'application contiennent des données
   personnelles : elles viennent toujours du réseau, et ne sont jamais conservées sur l'appareil. */
const CACHE = "rivezli-{{ version }}";
const PRECACHE = {{ precache|tojson }};
const OFFLINE = {{ offline|tojson }};
const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.startsWith("rivezli-") && k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim()));
});

// Fichier déjà connu : on le sert tout de suite, et on le rafraîchit en arrière-plan pour la prochaine fois.
function staleWhileRevalidate(request) {
  return caches.open(CACHE).then(cache => cache.match(request).then(cached => {
    const fresh = fetch(request).then(response => {
      if (response.ok || response.type === "opaque") cache.put(request, response.clone());
      return response;
    }).catch(() => cached);
    return cached || fresh;
  }));
}

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;                                  // formulaires, assistant : toujours le réseau
  const url = new URL(request.url);

  if (request.mode === "navigate") {                                     // une page : réseau, sinon page hors ligne
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE)));
    return;
  }
  if (FONT_HOSTS.includes(url.hostname)) { event.respondWith(staleWhileRevalidate(request)); return; }
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/video/")) return;                 // vidéo lourde, lue par morceaux : le navigateur s'en charge
  if (url.pathname.startsWith("/static/")) event.respondWith(staleWhileRevalidate(request));
  // tout le reste (données, JSON, téléchargements) : le réseau, sans cache
});
