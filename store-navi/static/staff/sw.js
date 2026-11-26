/* Cymbal Staff service worker: caches the app shell; network-first for everything (API never served stale
   unless offline, and API responses are not cached). */
const CACHE = "cymbal-staff-en-v14-1";
const SHELL = [
  "/", "/config.js", "/manifest.webmanifest",
  "/static/staff/css/staff.css", "/static/staff/js/staff.js",
  "/static/staff/icons/icon-192.png", "/static/staff/icons/icon-512.png", "/static/staff/icons/apple-touch-icon.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) {           // network-first, offline JSON fallback, never cached
    e.respondWith(fetch(req).catch(() => new Response(JSON.stringify({ detail: "You are offline" }),
      { status: 503, headers: { "Content-Type": "application/json" } })));
    return;
  }
  e.respondWith(fetch(req).then((res) => {           // network-first, refresh shell/static cache
    if (res.ok && (SHELL.includes(url.pathname) || url.pathname.startsWith("/static/") || url.pathname.startsWith("/img/"))) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
    }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: url.pathname === "/" }).then((r) => r || caches.match("/"))));
});
