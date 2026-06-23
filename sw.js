// Service Worker — Comptage Trafic SCET
// Permet à l'app de fonctionner 100% hors ligne après le premier chargement

const CACHE_NOM     = 'comptage-v2';
const FICHIERS_CACHE = [
  './',
  './index.html',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

// Installation : mettre en cache tous les fichiers de l'app
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NOM).then(cache => {
      return cache.addAll(FICHIERS_CACHE).catch(() => {
        // Si certains fichiers manquent, on continue quand même
        return Promise.resolve();
      });
    })
  );
  self.skipWaiting();
});

// Activation : nettoyer les anciens caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NOM).map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Interception des requêtes
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Pour les requêtes vers notre serveur Render : toujours réseau
  // Si hors ligne, l'app gère elle-même (stockage local)
  if (url.hostname.includes('onrender.com') ||
      url.hostname.includes('localhost') ||
      event.request.method !== 'GET') {
    return; // Pas d'interception
  }

  // Pour les fichiers de l'app : Cache d'abord, réseau si absent
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;

      return fetch(event.request).then(response => {
        // Mettre en cache la nouvelle ressource
        if (response && response.status === 200) {
          const copy = response.clone();
          caches.open(CACHE_NOM).then(cache => cache.put(event.request, copy));
        }
        return response;
      }).catch(() => {
        // Hors ligne et pas en cache : page d'erreur minimale
        if (event.request.headers.get('accept')?.includes('text/html')) {
          return caches.match('./index.html');
        }
      });
    })
  );
});
