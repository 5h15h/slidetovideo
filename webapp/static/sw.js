/* SlideToVideo service worker.
   The point here is the app shell, not the data: a render is minutes of local
   CPU, so there is nothing useful to do offline except load instantly and say
   plainly that the renderer is not running. */
const VERSION = "stv-v1";
const SHELL = [
  "/", "/app.css", "/app.js",
  "/assets/hero.webp", "/assets/ambient.jpg",
  "/assets/icon-192.png", "/assets/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(VERSION);
    // Don't let one missing file abort the whole install.
    await Promise.allSettled(SHELL.map(u => c.add(new Request(u, { cache: "reload" }))));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Never cache the API. Job state, slide images and the finished video are
  // all per-run, and a stale answer here would be worse than an error.
  if (url.pathname.startsWith("/api/")) return;

  // Navigations: fresh when the server is up, cached shell when it is not.
  if (req.mode === "navigate") {
    e.respondWith((async () => {
      try {
        return await fetch(req);
      } catch {
        return (await caches.match("/")) || Response.error();
      }
    })());
    return;
  }

  // Static assets: serve instantly, refresh in the background.
  e.respondWith((async () => {
    const cached = await caches.match(req);
    const network = fetch(req).then(async res => {
      if (res && res.ok) (await caches.open(VERSION)).put(req, res.clone());
      return res;
    }).catch(() => null);
    return cached || (await network) || Response.error();
  })());
});
