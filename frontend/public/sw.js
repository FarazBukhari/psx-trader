/**
 * PSX Signal System — Service Worker
 * Handles background push notifications and caching.
 */

const CACHE = 'psx-v1'

self.addEventListener('install', (e) => {
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(clients.claim())
})

// Handle push events (if using backend push in future)
self.addEventListener('push', (e) => {
  if (!e.data) return
  const data = e.data.json()
  e.waitUntil(
    self.registration.showNotification(data.title || 'PSX Signal', {
      body:  data.body  || '',
      icon:  '/favicon.svg',
      badge: '/favicon.svg',
      tag:   data.tag   || 'psx',
      data:  data,
    })
  )
})

self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(list => {
      for (const c of list) {
        if (c.url && 'focus' in c) return c.focus()
      }
      if (clients.openWindow) return clients.openWindow('/')
    })
  )
})
