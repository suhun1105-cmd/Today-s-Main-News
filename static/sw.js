const CACHE = 'tmn-v3';

self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // HTML과 API 응답은 항상 최신 서버 응답을 사용한다.
  if (url.pathname === '/' ||
      url.pathname.startsWith('/report') ||
      url.pathname.startsWith('/status') ||
      url.pathname.startsWith('/analyze') ||
      url.pathname.startsWith('/subscribe') ||
      url.pathname.startsWith('/unsubscribe') ||
      url.pathname.startsWith('/vapid-public-key')) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});

self.addEventListener('push', event => {
  let data = {
    title: '오늘의 뉴스 리포트가 준비됐습니다',
    body: '오전 9시 기준 뉴스 리포트가 생성됐습니다. 앱에서 확인하세요.',
  };

  try {
    data = event.data.json();
  } catch {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      vibrate: [200, 100, 200],
      requireInteraction: false,
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      if (list.length > 0) return list[0].focus();
      return clients.openWindow('/');
    })
  );
});
