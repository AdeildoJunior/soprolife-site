const CACHE_NAME = 'sl-20251107143626';
const ASSETS = [
  '/',
  '/index.html',
  '/favicon.svg?v=1',
  '/favicon-32.png?v=2',
  '/favicon-16.png?v=2',
  '/apple-touch-icon.png?v=2',
  '/manifest.webmanifest?v=1',
  '/og-image.svg?v=1',
  '/offline.html'
];

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
