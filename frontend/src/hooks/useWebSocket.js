/**
 * useWebSocket — manages the live WebSocket connection to the backend.
 * Auto-reconnects on disconnect with exponential backoff.
 */

import { useEffect, useRef, useState, useCallback } from 'react'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'
const MAX_RETRIES = 10

export function useWebSocket(onMessage) {
  const wsRef    = useRef(null)
  const retries  = useRef(0)
  const timerRef = useRef(null)
  const [status, setStatus]   = useState('connecting') // connecting | open | closed | error
  const [latency, setLatency] = useState(null)
  const pingTs   = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    setStatus('connecting')
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('open')
      retries.current = 0
      // Start ping loop
      timerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          pingTs.current = Date.now()
          ws.send('ping')
        }
      }, 5000)
    }

    ws.onmessage = (evt) => {
      if (evt.data === 'pong') {
        if (pingTs.current) setLatency(Date.now() - pingTs.current)
        return
      }
      try {
        const data = JSON.parse(evt.data)
        onMessage(data)
      } catch (_) {}
    }

    ws.onerror = () => setStatus('error')

    ws.onclose = () => {
      clearInterval(timerRef.current)
      setStatus('closed')
      if (retries.current < MAX_RETRIES) {
        const delay = Math.min(1000 * 2 ** retries.current, 30000)
        retries.current++
        setTimeout(connect, delay)
      }
    }
  }, [onMessage])

  useEffect(() => {
    connect()
    return () => {
      clearInterval(timerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { status, latency }
}
