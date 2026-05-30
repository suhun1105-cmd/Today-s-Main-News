const CACHE = 'tmn-v2';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/'])));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/analyze') ||
      e.request.url.includes('/status') ||
      e.request.url.includes('/report') ||
      e.request.url.includes('/subscribe') ||
      e.request.url.includes('/vapid')) {
    return;
  }
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});

// ── 푸시 알림 수신 ──
self.addEventListener('push', e => {
  let data = { title: "Today's Main News", body: "새 리포트가 준비됐습니다" };
  try { data = e.data.json(); } catch {}

  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      vibrate: [200, 100, 200],
      requireInteraction: false,
    })
  );
});

// ── 알림 탭하면 앱 열기 ──
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      if (list.length > 0) return list[0].focus();
      return clients.openWindow('/');
    })
  );
});
