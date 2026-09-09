/* A股短线仪表盘 · Service Worker
 * 策略：网络优先（保证打开即最新数据），断网时回退缓存（离线也能看）。
 */
const CACHE = 'a-stock-dashboard-v1';
const ASSETS = [
  './',
  './index.html',
  './data.js',
  './manifest.json',
  './dashboard_icon.png'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(ASSETS).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // 只处理同源资源，CDN 等外链放行
  if (url.origin !== self.location.origin) return;

  e.respondWith(
    fetch(req)
      .then(res => {
        if (res && res.status === 200) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then(r => r || caches.match('./index.html'))
      )
  );
});
