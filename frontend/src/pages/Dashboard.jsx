/**
 * Dashboard page — live signal monitoring.
 */

import { useMarketStore } from '../store/useMarketStore'
import SignalTable from '../components/dashboard/SignalTable'
import { PageLoader } from '../components/common/Loader'

export default function Dashboard() {
  const wsStatus   = useMarketStore((s) => s.wsStatus)
  const signals    = useMarketStore((s) => s.signals)
  const wsClients  = useMarketStore((s) => s.wsClients)
  const source     = useMarketStore((s) => s.source)
  const lastUpdate = useMarketStore((s) => s.lastUpdate)
  const horizon    = useMarketStore((s) => s.horizon)

  const lastFmt = lastUpdate
    ? new Date(lastUpdate).toLocaleTimeString('en-PK')
    : '—'

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
        <span className="ml-auto">
          Horizon:{' '}
          <span className={horizon === 'short' ? 'text-blue-400' : 'text-purple-400'}>
            {horizon.toUpperCase()}
          </span>
        </span>
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
