/**
 * useWebSocket — manages the live WebSocket connection.
 * Auto-reconnects with exponential backoff.
 * Writes all incoming data directly into useMarketStore.
 */

import { useEffect, useRef, useCallback } from 'react'
import { useMarketStore } from '../store/useMarketStore'
import { useUIStore }     from '../store/useUIStore'

// ── Browser notification helpers ──────────────────────────────────────────────
function requestNotifPermission() {
  if (typeof Notification === 'undefined') return
  if (Notification.permission === 'default') Notification.requestPermission()
}

function fireNotification(title, body, fallbackToast) {
  if (typeof Notification === 'undefined') { fallbackToast(`${title} — ${body}`); return }
  if (Notification.permission === 'granted') {
    try { new Notification(title, { body, icon: '/favicon.svg' }) } catch {}
  } else {
    fallbackToast(`${title} — ${body}`)
  }
}

const WS_URL      = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'
const MAX_RETRIES  = 12
const PING_MS      = 5000
const MAX_DELAY_MS = 10_000   // max reconnect delay = 10s (spec)

export function useWebSocket() {
  const wsRef   = useRef(null)
  const retries = useRef(0)
  const pingRef = useRef(null)
  const pingTs  = useRef(null)
  const mounted = useRef(true)

  const setWsStatus  = useMarketStore((s) => s.setWsStatus)
  const setLatency   = useMarketStore((s) => s.setLatency)
  const updateFromWS = useMarketStore((s) => s.updateFromWS)
  const showToast    = useUIStore((s) => s.showToast)

  const connect = useCallback(() => {
    if (!mounted.current) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    setWsStatus('connecting', retries.current > 0)
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mounted.current) { ws.close(); return }
      setWsStatus('open')
      retries.current = 0

      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          pingTs.current = Date.now()
          ws.send('ping')
        }
      }, PING_MS)
    }

    ws.onmessage = (evt) => {
      if (!mounted.current) return
      if (evt.data === 'pong') {
        if (pingTs.current) setLatency(Date.now() - pingTs.current)
        return
      }
      try {
        const data = JSON.parse(evt.data)
        if (data.type === 'snapshot' || data.type === 'update') {
          updateFromWS(data)
          // Fire notifications for actionable signal changes
          const changed = data.changed || []
          changed.forEach((s) => {
            const conf = s.prediction?.confidence ?? 0
            const isActionable = ['BUY', 'SELL', 'FORCE_SELL'].includes(s.signal)
            const isHighConf   = conf >= 0.7
            if (isActionable || isHighConf) {
              const price = s.current ? ` @ PKR ${s.current.toFixed(2)}` : ''
              const confStr = isHighConf ? ` (${(conf * 100).toFixed(0)}% conf)` : ''
              fireNotification(
                `${s.signal}: ${s.symbol}${price}`,
                `${s.sector || ''}${confStr}`,
                (msg) => showToast(msg, s.signal === 'BUY' ? 'success' : 'warning'),
              )
            }
          })
        }
      } catch (_) { /* ignore malformed frames */ }
    }

    ws.onerror = () => {
      if (!mounted.current) return
      setWsStatus('error')
    }

    ws.onclose = () => {
      clearInterval(pingRef.current)
      if (!mounted.current) return
      setWsStatus('closed')
      if (retries.current < MAX_RETRIES) {
        const delay = Math.min(1000 * Math.pow(2, retries.current), MAX_DELAY_MS)
        retries.current++
        setTimeout(connect, delay)
      }
    }
  }, [setWsStatus, setLatency, updateFromWS])

  useEffect(() => {
    mounted.current = true
    requestNotifPermission()
    connect()
    return () => {
      mounted.current = false
      clearInterval(pingRef.current)
      wsRef.current?.close()
    }
  }, [connect])
}
