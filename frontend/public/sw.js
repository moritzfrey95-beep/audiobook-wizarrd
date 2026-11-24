// Service Worker von ABW: nur für Push-Meldungen und das Öffnen der passenden Seite. Er speichert nichts zwischen,
// die Oberfläche kommt immer frisch vom Server.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()))

self.addEventListener('push', e => {
  let m = {}
  try { m = e.data ? e.data.json() : {} } catch { m = { body: e.data ? e.data.text() : '' } }
  const show = self.registration.showNotification(m.title || 'AudioBook Wizarrd', {
    body: m.body || '', tag: m.tag, icon: '/icon-192.png', badge: '/badge-96.png', lang: 'de', data: { url: m.url || '/benachrichtigungen' },
  })
  const badge = 'setAppBadge' in navigator && typeof m.unread === 'number'
    ? (m.unread ? navigator.setAppBadge(m.unread) : navigator.clearAppBadge()).catch(() => {}) : Promise.resolve()
  e.waitUntil(Promise.all([show, badge]))
})

self.addEventListener('notificationclick', e => {
  e.notification.close()
  const url = new URL(e.notification.data?.url || '/', self.location.origin).href
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const win = wins.find(w => new URL(w.url).origin === self.location.origin)
    if (win) { await win.focus(); return win.navigate(url).catch(() => self.clients.openWindow(url)) }
    return self.clients.openWindow(url)
  })())
})
