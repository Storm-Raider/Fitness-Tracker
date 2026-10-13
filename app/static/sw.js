const CACHE_VERSION = 'v0.5.0';
const CACHE_NAME = `fittrack-${CACHE_VERSION}`;
// Copies of pages you have opened, so they still open with no signal. Kept
// across worker versions; cleared whenever the login page is shown, so the
// next person to sign in on this device never sees the previous one's pages.
const PAGES_CACHE = 'zenkai-pages-v1';

const PRECACHE = [
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

// Paths that mean "signed out or signing in": never cached, and showing one
// clears the saved pages.
const SIGNED_OUT = ['/login', '/logout', '/forgot-password', '/reset-password/', '/invite/accept/'];

const OFFLINE_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f4f4f1"><meta name="color-scheme" content="light">
<title>Offline — Zenkai</title>
<style>
  /* Daylight. No webfont link: the server is unreachable, so fall back to system-ui. */
  :root { color-scheme: light; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #f4f4f1; color: #141414; padding: 16px;
         font-family: 'Atkinson Hyperlegible Next', system-ui, sans-serif; font-size: 16px; line-height: 1.5; }
  main { width: 100%; max-width: 22rem; text-align: center; }
  h1 { font-family: 'Archivo', system-ui, sans-serif; font-weight: 800; font-size: 1.75rem;
       letter-spacing: -0.01em; line-height: 1.15; margin: 0 0 0.5rem; }
  p { color: #5d5d58; margin: 0 0 1.5rem; }
  button { min-height: 56px; width: 100%; border: 0; border-radius: 18px; background: #ff5a1f;
           color: #141414; font: inherit; font-weight: 700; font-size: 1.0625rem; cursor: pointer; }
  button:hover { background: #f04e14; }
  button:focus-visible { outline: 2px solid #141414; outline-offset: 2px; }
</style></head>
<body><main>
  <h1>You're offline</h1>
  <p>Zenkai can't reach the server. Pages you have opened before still work offline; this one hasn't been opened on this device yet.</p>
  <button onclick="location.reload()">Try again</button>
</main></body></html>`;

function offlineResponse() {
  return new Response(OFFLINE_HTML, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const old = (await caches.keys()).filter(k => k !== CACHE_NAME && k !== PAGES_CACHE);
    await Promise.all(old.map(k => caches.delete(k)));
    await self.clients.claim();
    // Force every open window to reload once when a NEW worker version takes
    // over, so a device stuck on a stale cached page (common with installed
    // iOS PWAs) self-heals on the next launch instead of needing a manual
    // cache clear. Fires once per version bump (an old version's cache is
    // there to replace) — no reload loop. Not on a first install: that
    // reloaded the login page a moment after it opened, cancelling a sign-in
    // already sent.
    if (!old.length) return;
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach(c => { try { c.navigate(c.url); } catch (e) {} });
  })());
});

async function navigate(request, url) {
  if (SIGNED_OUT.some(p => url.pathname === p || (p.endsWith('/') && url.pathname.startsWith(p)))) {
    await caches.delete(PAGES_CACHE);
    return fetch(request).catch(offlineResponse);
  }
  try {
    const response = await fetch(request);
    const type = response.headers.get('Content-Type') || '';
    // Only a real page: not an error, not a redirect to the login page.
    if (response.ok && !response.redirected && type.startsWith('text/html')) {
      const copy = response.clone();
      caches.open(PAGES_CACHE).then(cache => cache.put(request, copy));
    }
    return response;
  } catch (e) {
    const cached = await caches.match(request, { cacheName: PAGES_CACHE });
    return cached || offlineResponse();
  }
}

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

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
  } else if (event.request.mode === 'navigate') {
    // Network-first for pages; the saved copy (or an offline page) when the Pi
    // is unreachable.
    event.respondWith(navigate(event.request, url));
  }
  // Everything else (fetch/HTMX/API calls, event streams) goes straight to the
  // network, so the page's own code sees the failure and can say so.
});
