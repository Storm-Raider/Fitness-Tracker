const CACHE_VERSION = 'v0.4.0';
const CACHE_NAME = `fittrack-${CACHE_VERSION}`;

const PRECACHE = [
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
      // Force every open window to reload once when a NEW worker version takes
      // over, so a device stuck on a stale cached page (common with installed
      // iOS PWAs) self-heals on the next launch instead of needing a manual
      // cache clear. Fires once per version bump — no reload loop.
      .then(() => self.clients.matchAll({ type: 'window' }))
      .then(clients => clients.forEach(c => { try { c.navigate(c.url); } catch (e) {} }))
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  if (url.pathname.startsWith('/static/')) {
    // Cache-first for static assets
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        });
      })
    );
  } else {
    // Network-first for pages — fall back to cache when Pi is unreachable
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  }
});
