/**
 * PSX Smart Signal System — Main App
 */

import { useState, useCallback } from 'react'
import { useWebSocket } from './hooks/useWebSocket.js'
import { useNotifications } from './hooks/useNotifications.js'
import StatusBar from './components/StatusBar.jsx'
import SummaryCards from './components/SummaryCards.jsx'
import StockTable from './components/StockTable.jsx'

const SIGNAL_FILTER_OPTIONS = ['ALL', 'BUY', 'SELL', 'FORCE_SELL', 'HOLD']
const API = 'http://localhost:8000'

export default function App() {
  const [signals, setSignals]           = useState([])
  const [lastUpdate, setLastUpdate]     = useState(null)
  const [source, setSource]             = useState('unknown')
  const [wsClients, setWsClients]       = useState(0)
  const [filter, setFilter]             = useState('')
  const [signalFilter, setSignalFilter] = useState('ALL')
  const [smartSort, setSmartSort]       = useState(true)
  const [sortKey, setSortKey]           = useState('action_score')
  const [sortDir, setSortDir]           = useState('desc')
  const [changeLog, setChangeLog]       = useState([])
  const [configLoadedAt, setConfigLoadedAt] = useState(null)
  const [horizon, setHorizon]           = useState('short')  // "short" | "long"

  const { permission, requestPermission, notify } = useNotifications()

  const handleMessage = useCallback((data) => {
    if (data.type === 'snapshot' || data.type === 'update') {
      setSignals(data.all || [])
      setLastUpdate(data.timestamp)
      setSource(data.source || 'unknown')
      setWsClients(data.client_count || 0)
      if (data.horizon)   setHorizon(data.horizon)
      if (data.config_at) setConfigLoadedAt(data.config_at)

      if (data.changed && data.changed.length) {
        data.changed.forEach(s => {
          if (s.signal === 'BUY' || s.signal === 'SELL' || s.signal === 'FORCE_SELL') {
            notify(
              `${s.signal}: ${s.symbol}`,
              `Price: ${s.current ? s.current.toFixed(2) : '?'} | Chg: ${s.change_pct ? s.change_pct.toFixed(2) : '?'}%`,
              s.symbol
            )
          }
          setChangeLog(prev => [{
            symbol:  s.symbol,
            signal:  s.signal,
            prev:    s.prev_signal,
            price:   s.current,
            chg_pct: s.change_pct,
            ts:      new Date().toLocaleTimeString(),
          }, ...prev.slice(0, 19)])
        })
      }
    }
  }, [notify])

  const { status, latency } = useWebSocket(handleMessage)

  const handleSort = (col) => {
    setSmartSort(false)
    if (col === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(col); setSortDir('desc') }
  }

  const handleSmartSort = () => {
    setSmartSort(true)
    setSortKey('action_score')
    setSortDir('desc')
  }

  const handleHorizon = async (h) => {
    setHorizon(h)
    // Tell backend to recompute scores
    try {
      await fetch(`${API}/api/horizon/${h}`, { method: 'POST' })
    } catch (_) {}
    // Immediately re-sort frontend with new scores that arrive on next WS tick
    if (smartSort) {
      setSortKey('action_score')
      setSortDir('desc')
    }
  }

  const displaySignals = signalFilter === 'ALL'
    ? signals
    : signals.filter(s => s.signal === signalFilter)

  const horizonMeta = {
    short: {
      label: 'SHORT-TERM',
      desc: 'Ranks by: volume liquidity · price momentum · intraday volatility · freshness',
      color: 'bg-blue-500/20 text-blue-300 border-blue-600',
    },
    long: {
      label: 'LONG-TERM',
      desc: 'Ranks by: RSI extremes · price threshold hits · SMA crossovers · strategy agreement',
      color: 'bg-purple-500/20 text-purple-300 border-purple-600',
    },
  }[horizon]

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-gray-950 border-b border-gray-800">
        <div className="px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">📈</span>
            <div>
              <h1 className="text-lg font-black text-white tracking-tight">PSX Signal System</h1>
              <p className="text-xs text-gray-500">Pakistan Stock Exchange — Live Trading Signals</p>
            </div>
          </div>
          {/* Horizon toggle */}
          <div className="flex items-center gap-2 bg-gray-900 border border-gray-700 rounded-lg p-1">
            <button
              onClick={() => handleHorizon('short')}
              className={`px-4 py-1.5 rounded text-xs font-bold transition ${
                horizon === 'short'
                  ? 'bg-blue-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              ⚡ SHORT-TERM
            </button>
            <button
              onClick={() => handleHorizon('long')}
              className={`px-4 py-1.5 rounded text-xs font-bold transition ${
                horizon === 'long'
                  ? 'bg-purple-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              📅 LONG-TERM
            </button>
          </div>
        </div>
        <StatusBar
          status={status}
          latency={latency}
          lastUpdate={lastUpdate}
          source={source}
          wsClients={wsClients}
          permission={permission}
          onRequestNotif={requestPermission}
          configLoadedAt={configLoadedAt}
        />
      </header>

      <main className="flex-1 px-6 py-5 space-y-5 max-w-screen-2xl mx-auto w-full">
        <SummaryCards signals={signals} />

        {/* Controls row */}
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Search symbol, sector…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 w-52"
          />
          <div className="flex gap-1">
            {SIGNAL_FILTER_OPTIONS.map(opt => (
              <button
                key={opt}
                onClick={() => setSignalFilter(opt)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition ${
                  signalFilter === opt
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >
                {opt}
              </button>
            ))}
          </div>
          <button
            onClick={handleSmartSort}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-bold transition border ${
              smartSort
                ? 'bg-yellow-500/20 text-yellow-300 border-yellow-600'
                : 'bg-gray-800 text-gray-400 border-gray-700 hover:bg-gray-700'
            }`}
          >
            ⚡ SMART SORT {smartSort ? 'ON' : 'OFF'}
          </button>
          <span className="text-xs text-gray-600 ml-auto">{displaySignals.length} stocks</span>
        </div>

        {/* Active horizon description */}
        {smartSort && (
          <div className={`text-xs border rounded-lg px-4 py-2 flex items-center gap-3 ${horizonMeta.color}`}>
            <span className="font-bold">{horizonMeta.label} MODE</span>
            <span className="text-gray-400">|</span>
            <span className="opacity-80">{horizonMeta.desc}</span>
          </div>
        )}

        <StockTable
          signals={displaySignals}
          filter={filter}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={handleSort}
          smartSort={smartSort}
          horizon={horizon}
        />

        {changeLog.length > 0 && (
          <div>
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-2">
              ⚡ Recent Signal Changes
            </h2>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {changeLog.map((e, i) => (
                <div key={i} className="flex items-center gap-3 text-xs bg-gray-900/60 rounded px-3 py-1.5 border border-gray-800">
                  <span className="text-gray-600 font-mono w-16">{e.ts}</span>
                  <span className="font-bold text-white w-12">{e.symbol}</span>
                  <span className="text-gray-500">{e.prev} →</span>
                  <span className={`font-bold ${
                    e.signal === 'BUY' ? 'text-green-400'
                    : e.signal === 'SELL' || e.signal === 'FORCE_SELL' ? 'text-red-400'
                    : 'text-gray-400'
                  }`}>{e.signal}</span>
                  <span className="text-gray-400 ml-auto font-mono">
                    {e.price ? e.price.toFixed(2) : '?'} ({e.chg_pct >= 0 ? '+' : ''}{e.chg_pct ? e.chg_pct.toFixed(2) : '?'}%)
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>

      <footer className="px-6 py-3 border-t border-gray-800 text-xs text-gray-700 text-center">
        PSX Signal System — For informational purposes only. Not financial advice.
      </footer>
    </div>
  )
}
