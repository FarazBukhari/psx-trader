/**
 * StatusBar — connection status, data source, config reload button + toast.
 */

import { useState, useEffect } from 'react'

export default function StatusBar({ status, latency, lastUpdate, source, wsClients, permission, onRequestNotif, configLoadedAt }) {
  const [reloading, setReloading] = useState(false)
  const [toast, setToast]         = useState(null)  // { msg, type }

  // Show toast when config reloads (configLoadedAt changes)
  useEffect(() => {
    if (!configLoadedAt) return
    setToast({ msg: '✅ strategy.json reloaded', type: 'success' })
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [configLoadedAt])

  const handleReload = async () => {
    setReloading(true)
    try {
      const r = await fetch('http://localhost:8000/api/config/reload', { method: 'POST' })
      if (r.ok) {
        setToast({ msg: '✅ Config reloaded', type: 'success' })
      } else {
        setToast({ msg: '❌ Reload failed', type: 'error' })
      }
    } catch {
      setToast({ msg: '❌ Backend unreachable', type: 'error' })
    } finally {
      setReloading(false)
      setTimeout(() => setToast(null), 4000)
    }
  }

  const dot = {
    open:       'bg-green-400 animate-pulse',
    connecting: 'bg-yellow-400 animate-pulse',
    closed:     'bg-red-500',
    error:      'bg-red-500',
  }[status] || 'bg-gray-500'

  const label = {
    open:       'LIVE',
    connecting: 'Connecting…',
    closed:     'Reconnecting…',
    error:      'Error',
  }[status] || status.toUpperCase()

  const ts = lastUpdate ? new Date(lastUpdate * 1000).toLocaleTimeString() : '—'
  const configTs = configLoadedAt ? new Date(configLoadedAt * 1000).toLocaleTimeString() : '—'

  return (
    <div className="relative">
      {/* Toast */}
      {toast && (
        <div className={`absolute top-10 right-4 z-50 px-4 py-2 rounded-lg text-xs font-semibold shadow-lg border ${
          toast.type === 'success'
            ? 'bg-green-900/80 text-green-300 border-green-700'
            : 'bg-red-900/80 text-red-300 border-red-700'
        }`}>
          {toast.msg}
        </div>
      )}

      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800 text-xs text-gray-400">
        {/* Left: connection + source */}
        <div className="flex items-center gap-3">
          <span className={`w-2 h-2 rounded-full ${dot}`} />
          <span className="font-mono font-semibold text-gray-200">{label}</span>
          {latency != null && <span className="text-gray-500">{latency}ms</span>}
          <span className="text-gray-700">|</span>
          <span>
            Source:{' '}
            <span className={`font-semibold ${source === 'mock' ? 'text-yellow-400' : 'text-green-400'}`}>
              {source === 'mock' ? 'MOCK DATA' : 'PSX LIVE'}
            </span>
          </span>
          {source === 'mock' && (
            <span className="bg-yellow-900/40 text-yellow-300 border border-yellow-700 px-2 py-0.5 rounded text-[10px]">
              ⚠ Scraper fell back to mock — check backend logs
            </span>
          )}
          <span className="text-gray-700">|</span>
          <span>Config: <span className="text-gray-300 font-mono">{configTs}</span></span>
        </div>

        {/* Right: meta + buttons */}
        <div className="flex items-center gap-3">
          <span>Updated: <span className="text-gray-300 font-mono">{ts}</span></span>
          <span>{wsClients} viewer{wsClients !== 1 ? 's' : ''}</span>

          {/* Reload config button */}
          <button
            onClick={handleReload}
            disabled={reloading}
            title="Reload strategy.json from disk"
            className="bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-300 border border-gray-600 px-3 py-1 rounded text-[11px] font-semibold transition flex items-center gap-1.5"
          >
            <span className={reloading ? 'animate-spin' : ''}>⚙</span>
            {reloading ? 'Reloading…' : 'Reload Config'}
          </button>

          {/* Notification button */}
          {permission === 'default' && (
            <button
              onClick={onRequestNotif}
              className="bg-blue-700 hover:bg-blue-600 text-white px-3 py-1 rounded text-[11px] font-semibold transition"
            >
              🔔 Enable Alerts
            </button>
          )}
          {permission === 'granted' && <span className="text-green-400 text-[11px]">🔔 Alerts ON</span>}
          {permission === 'denied'  && <span className="text-red-400 text-[11px]">🔕 Alerts blocked</span>}
        </div>
      </div>
    </div>
  )
}
