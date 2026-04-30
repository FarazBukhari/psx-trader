/**
 * Header — app header with tabs, market status, horizon toggle, WS badge.
 * Also renders the stale-data banner and the global toast.
 */

import { useEffect, useState, useCallback } from 'react'
import clsx from 'clsx'
import Tabs from './Tabs'
import { useMarketStore } from '../../store/useMarketStore'
import { useUIStore }     from '../../store/useUIStore'
import { setHorizon as apiSetHorizon, getSystemStatus } from '../../api/system'

// ── Market countdown timer ────────────────────────────────────────────────────
function useCountdown(targetSeconds) {
  const [remaining, setRemaining] = useState(targetSeconds)
  useEffect(() => {
    setRemaining(targetSeconds)
    if (!targetSeconds || targetSeconds <= 0) return
    const id = setInterval(() => setRemaining((r) => Math.max(0, r - 1)), 1000)
    return () => clearInterval(id)
  }, [targetSeconds])
  return remaining
}

function fmtCountdown(secs) {
  if (!secs || secs <= 0) return null
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  return `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`
}

// ── WS connection status dot + label ─────────────────────────────────────────
const CONN_DOT = {
  connected:    'bg-green-500',
  reconnecting: 'bg-yellow-400 animate-pulse',
  connecting:   'bg-yellow-400 animate-pulse',
  disconnected: 'bg-red-500',
}
const CONN_LABEL = {
  connected:    'Live',
  reconnecting: 'Reconnecting…',
  connecting:   'Connecting…',
  disconnected: 'Disconnected',
}

// ── Toast notification ────────────────────────────────────────────────────────
const TOAST_STYLE = {
  success: 'bg-green-900/90 border-green-700 text-green-200',
  error:   'bg-red-900/90 border-red-700 text-red-200',
  warning: 'bg-yellow-900/90 border-yellow-700 text-yellow-200',
  info:    'bg-blue-900/90 border-blue-700 text-blue-200',
}

function Toast({ toast, onClose }) {
  if (!toast) return null
  return (
    <div
      className={clsx(
        'fixed bottom-5 right-5 z-50 flex items-start gap-3 px-4 py-3 rounded-lg border shadow-xl max-w-sm',
        TOAST_STYLE[toast.type] || TOAST_STYLE.info,
      )}
    >
      <span className="flex-1 text-sm">{toast.msg}</span>
      <button onClick={onClose} className="opacity-60 hover:opacity-100 text-xs mt-0.5">✕</button>
    </div>
  )
}

// ── Main Header ───────────────────────────────────────────────────────────────
export default function Header() {
  const wsStatus        = useMarketStore((s) => s.wsStatus)
  const connectionStatus = useMarketStore((s) => s.connectionStatus)
  const latency         = useMarketStore((s) => s.latency)
  const horizon    = useMarketStore((s) => s.horizon)
  const dataStale  = useMarketStore((s) => s.dataStale)
  const staleReason= useMarketStore((s) => s.staleReason)
  const lastUpdate = useMarketStore((s) => s.lastUpdate)
  const setHorizonStore = useMarketStore((s) => s.setHorizon)
  const setSystemStatus = useMarketStore((s) => s.setSystemStatus)
  const systemStatus    = useMarketStore((s) => s.systemStatus)

  const toast      = useUIStore((s) => s.toast)
  const clearToast = useUIStore((s) => s.clearToast)
  const showToast  = useUIStore((s) => s.showToast)

  // No-update staleness — fire only if no WS tick for 2× the poll interval (10s)
  const [wsStale, setWsStale] = useState(false)
  useEffect(() => {
    if (!lastUpdate) return
    setWsStale(false)
    const id = setTimeout(() => setWsStale(true), 20_000)
    return () => clearTimeout(id)
  }, [lastUpdate])

  // Poll system status every 60s
  const pollStatus = useCallback(() => {
    getSystemStatus()
      .then(setSystemStatus)
      .catch(() => {})
  }, [setSystemStatus])

  useEffect(() => {
    pollStatus()
    const id = setInterval(pollStatus, 60_000)
    return () => clearInterval(id)
  }, [pollStatus])

  const mkt = systemStatus?.market
  const secsToOpen  = mkt?.seconds_to_open
  const secsToClose = mkt?.seconds_to_close
  const isOpen      = mkt?.is_open
  const countdown   = useCountdown(isOpen ? secsToClose : secsToOpen)

  const handleHorizon = async (h) => {
    setHorizonStore(h)
    try {
      await apiSetHorizon(h)
    } catch (e) {
      showToast(`Failed to switch horizon: ${e.message}`, 'error')
    }
  }

  return (
    <>
      {/* ── App bar ── */}
      <header className="bg-gray-950 border-b border-gray-800">
        <div className="px-5 py-3 flex items-center justify-between gap-4">
          {/* Logo */}
          <div className="flex items-center gap-2.5 shrink-0">
            <span className="text-xl">📈</span>
            <div>
              <h1 className="text-base font-black text-white tracking-tight leading-tight">PSX Trader</h1>
              <p className="text-[10px] text-gray-600 leading-tight">Pakistan Stock Exchange</p>
            </div>
          </div>

          {/* Tabs */}
          <div className="flex-1">
            <Tabs />
          </div>

          {/* Right controls */}
          <div className="flex items-center gap-3 shrink-0">
            {/* Horizon toggle */}
            <div className="flex items-center gap-1 bg-gray-900 border border-gray-700 rounded-lg p-0.5">
              {['short', 'long'].map((h) => (
                <button
                  key={h}
                  onClick={() => handleHorizon(h)}
                  className={clsx(
                    'px-3 py-1 rounded text-xs font-bold transition',
                    horizon === h
                      ? h === 'short' ? 'bg-blue-600 text-white' : 'bg-purple-600 text-white'
                      : 'text-gray-500 hover:text-gray-300',
                  )}
                >
                  {h === 'short' ? '⚡ SHORT' : '📅 LONG'}
                </button>
              ))}
            </div>

            {/* Market status */}
            {mkt && (
              <div className={clsx(
                'flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-bold border',
                isOpen
                  ? 'bg-green-500/10 text-green-400 border-green-800'
                  : 'bg-gray-800 text-gray-500 border-gray-700',
              )}>
                <span className={clsx('w-1.5 h-1.5 rounded-full', isOpen ? 'bg-green-400 animate-pulse' : 'bg-gray-600')} />
                {mkt.phase_label || (isOpen ? 'OPEN' : 'CLOSED')}
                {countdown > 0 && (
                  <span className="text-gray-500 font-mono ml-1">{fmtCountdown(countdown)}</span>
                )}
              </div>
            )}

            {/* WS status */}
            <div className={clsx(
              'flex items-center gap-1.5 text-xs px-2 py-1 rounded border',
              connectionStatus === 'connected'    ? 'text-green-400 border-green-900 bg-green-950/40'
              : connectionStatus === 'reconnecting' ? 'text-yellow-400 border-yellow-900 bg-yellow-950/40'
              : 'text-red-400 border-red-900 bg-red-950/40',
            )}>
              <span className={clsx('w-2 h-2 rounded-full', CONN_DOT[connectionStatus] || 'bg-gray-600')} />
              <span className="hidden sm:inline font-medium">
                {connectionStatus === 'connected' && latency ? `Live · ${latency}ms` : CONN_LABEL[connectionStatus] || connectionStatus}
              </span>
            </div>
          </div>
        </div>

        {/* ── Stale data banner ── */}
        {(dataStale || wsStale) && (
          <div className="bg-yellow-900/40 border-t border-yellow-800 px-5 py-1.5 flex items-center gap-2 text-xs text-yellow-300">
            <span>⚠</span>
            <span>
              {wsStale && !dataStale
                ? 'No data received in 10s — connection may be stale.'
                : `Stale data — prices from snapshot. ${staleReason || ''}`}
            </span>
          </div>
        )}
      </header>

      {/* ── Global toast ── */}
      <Toast toast={toast} onClose={clearToast} />
    </>
  )
}
