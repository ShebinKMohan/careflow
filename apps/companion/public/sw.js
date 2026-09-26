/*
 * CareFlow companion service worker. It caches only the static app shell: the start page, the
 * manifest, the icons and Next's hashed static assets. Everything else, above all every API
 * request (another origin, or any /v1/ path), is network-only and never cached, because API
 * responses can carry patient data. Research prototype. Synthetic data only.
 */
const CACHE = "careflow-companion-shell-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/");
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // Network-only (never cached): other origins (the API), non-GET requests, API paths, RSC data.
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/v1/")) return;
  if (request.headers.has("RSC") || url.searchParams.has("_rsc")) return;

  if (request.mode === "navigate") {
    // Pages: network first; offline, fall back to the cached start page only.
    if (url.pathname !== "/") return;
    event.respondWith(fetch(request).catch(() => caches.match("/")));
    return;
  }
  if (isStaticAsset(url) || url.pathname === "/manifest.webmanifest") {
    // Hashed assets and icons: cache first.
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ??
          fetch(request).then((response) => {
            if (response.ok && isStaticAsset(url)) {
              const copy = response.clone();
              event.waitUntil(caches.open(CACHE).then((cache) => cache.put(request, copy)));
            }
            return response;
          }),
      ),
    );
  }
});
