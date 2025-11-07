const CACHE_NAME = 'sl-20251107161624';
const ASSETS = [ '/', '/index.html', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png', '/maskable-512.png', '/img/whatsapp.svg', '/img/sopro-logo.svg', '/favicon.svg?v=1', '/favicon-32.png?v=2', '/favicon-16.png?v=2', '/apple-touch-icon.png?v=2', '/offline.html' ];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.map(k => k.startsWith('sl-') && k !== CACHE_NAME ? caches.delete(k) : null))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(resp => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        return resp;
      })
      .catch(() => caches.match(e.request).then(m => m || caches.match('/offline.html')))
  );
});
