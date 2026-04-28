/**
 * useNotifications — manages Web Push Notification permission + dispatch.
 */

import { useCallback, useEffect, useState } from 'react'

export function useNotifications() {
  const [permission, setPermission] = useState(
    typeof Notification !== 'undefined' ? Notification.permission : 'unsupported'
  )

  useEffect(() => {
    if (typeof Notification === 'undefined') return
    setPermission(Notification.permission)
  }, [])

  const requestPermission = useCallback(async () => {
    if (typeof Notification === 'undefined') return 'unsupported'
    const result = await Notification.requestPermission()
    setPermission(result)
    return result
  }, [])

  const notify = useCallback((title, body, tag) => {
    if (permission !== 'granted') return
    try {
      const n = new Notification(title, {
        body,
        tag,
        icon: '/favicon.svg',
        badge: '/favicon.svg',
        silent: false,
      })
      // Auto-close after 8s
      setTimeout(() => n.close(), 8000)
    } catch (_) {}
  }, [permission])

  return { permission, requestPermission, notify }
}
