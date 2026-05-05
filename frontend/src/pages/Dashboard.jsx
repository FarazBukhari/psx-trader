/**
 * Dashboard page — live signal monitoring.
 */

import clsx from 'clsx'
import { useMarketStore } from '../store/useMarketStore'
import { useUIStore }     from '../store/useUIStore'
import { setStrategy as apiSetStrategy } from '../api/system'
import SignalTable from '../components/dashboard/SignalTable'
import { PageLoader } from '../components/common/Loader'

const PRESETS = [
  { id: 'conservative', label: '🛡 Conservative', desc: 'RSI 25/75 · SMA 10/30 · Fewer, high-conviction signals' },
  { id: 'default',      label: '⚖ Balanced',      desc: 'RSI 30/70 · SMA 5/20 · Standard' },
  { id: 'aggressive',   label: '⚡ Aggressive',    desc: 'RSI 35/65 · SMA 3/10 · More signals, higher variance' },
  { id: 'momentum',     label: '🚀 Momentum',      desc: 'RSI 40/60 · SMA 5/15 · Trend-chasing, very reactive' },
]

export default function Dashboard() {
  const wsStatus         = useMarketStore((s) => s.wsStatus)
  const signals          = useMarketStore((s) => s.signals)
  const wsClients        = useMarketStore((s) => s.wsClients)
  const source           = useMarketStore((s) => s.source)
  const lastUpdate       = useMarketStore((s) => s.lastUpdate)
  const strategy         = useMarketStore((s) => s.strategy)
  const setStrategyStore = useMarketStore((s) => s.setStrategy)
  const showToast        = useUIStore((s) => s.showToast)

  const lastFmt = lastUpdate
    ? new Date(lastUpdate).toLocaleTimeString('en-PK')
    : '—'

  const handleStrategy = async (name) => {
    setStrategyStore(name)
    try {
      await apiSetStrategy(name)
    } catch (e) {
      showToast(`Failed to switch strategy: ${e.message}`, 'error')
    }
  }

  return (
    <div className="px-5 py-5 space-y-4 max-w-screen-2xl mx-auto w-full">
      {/* Sub-header strip */}
      <div className="flex flex-wrap items-center gap-4 text-xs text-gray-600">
        <span>
          {wsStatus === 'open'
            ? <span className="text-green-500">● Live</span>
            : wsStatus === 'connecting'
            ? <span className="text-yellow-400">◌ Connecting…</span>
            : <span className="text-red-500">● Disconnected</span>}
        </span>
        {signals.length > 0 && <span>{signals.length} symbols tracked</span>}
        {wsClients > 0 && <span>{wsClients} WS client{wsClients !== 1 ? 's' : ''}</span>}
        {source !== 'unknown' && <span>Source: {source}</span>}
        {lastUpdate && <span>Updated: {lastFmt}</span>}
      </div>

      {/* Strategy selector */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-gray-500 font-medium shrink-0">Strategy</span>
        <div className="flex items-center gap-1 bg-gray-900 border border-gray-700 rounded-lg p-0.5">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              onClick={() => handleStrategy(p.id)}
              title={p.desc}
              className={clsx(
                'px-2.5 py-1 rounded text-xs font-bold transition',
                strategy === p.id
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300',
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        {['aggressive', 'momentum'].includes(strategy) && (
          <span className="text-yellow-600 text-[10px]">⚠ High volume</span>
        )}
      </div>

      {/* WS error fallback */}
      {(wsStatus === 'error' || wsStatus === 'closed') && signals.length === 0 && (
        <div className="px-4 py-3 bg-red-900/30 border border-red-800 rounded-lg text-sm text-red-300">
          WebSocket {wsStatus} — signal data unavailable. Reconnecting automatically…
        </div>
      )}

      {/* Initial connecting state */}
      {wsStatus === 'connecting' && signals.length === 0 && (
        <PageLoader message="Connecting to live feed…" />
      )}

      <SignalTable />
    </div>
  )
}
