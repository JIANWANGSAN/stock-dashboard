/* A股短线仪表盘 · Service Worker
 * 策略：
 *   - 静态资源（echarts.min.js / 图标 / manifest）→ 缓存优先（秒开，且离线可用）
 *   - 页面与数据（index.html / data.js / data.json）→ 网络优先（保证最新），失败回退缓存
 */
const CACHE = 'a-stock-dashboard-v3';
const PRECACHE = ['./', './index.html', './echarts.min.js', './manifest.json', './dashboard_icon.png'];
const STATIC_RE = /(echarts\.min\.js|dashboard_icon\.png|manifest\.json)$/;

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(PRECACHE).catch(() => null))
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
  if (url.origin !== self.location.origin) return;   // 外链（东财/腾讯行情等）放行

  // 静态资源：缓存优先
  if (STATIC_RE.test(url.pathname)) {
    e.respondWith(
      caches.match(req).then(r => r || fetch(req).then(res => {
        if (res && res.status === 200) {
          const cp = res.clone();
          caches.open(CACHE).then(c => c.put(req, cp));
        }
        return res;
      }))
    );
    return;
  }

  // 页面 / 数据：网络优先，失败回退缓存
  e.respondWith(
    fetch(req)
      .then(res => {
        if (res && res.status === 200) {
          const cp = res.clone();
          caches.open(CACHE).then(c => c.put(req, cp));
        }
        return res;
      })
      .catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
  );
});
