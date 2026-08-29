/**
 * TG Power Suite Service Worker
 * Provides offline shell caching, instant PWA launches, and background update checks.
 */

const CACHE_NAME = 'tg-power-suite-v2.8.4';
const STATIC_ASSETS = [
  '/',
  '/css/style.css',
  '/js/app.bundle.js',
  '/js/socket.io.min.js',
  '/manifest.json',
  '/assets/icon-192.png',
  '/assets/icon-512.png',
  '/assets/icon.svg',
  '/assets/favicon.ico'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Bypass service worker for dynamic backend APIs, streaming video/audio downloads, and Socket.IO
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/dl/') ||
    url.pathname.startsWith('/stream/') ||
    url.pathname.startsWith('/socket.io/') ||
    event.request.method !== 'GET' ||
    event.request.headers.has('range')
  ) {
    return;
  }

  // Network-First for main page HTML, Cache-First for static assets
  if (url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const resClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, resClone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        // Fetch in background to update cache
        fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, networkResponse));
          }
        }).catch(() => {});
        return cachedResponse;
      }
      return fetch(event.request).then((response) => {
        if (!response || response.status !== 200 || response.type !== 'basic') {
          return response;
        }
        const responseToCache = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseToCache));
        return response;
      });
    })
  );
});
