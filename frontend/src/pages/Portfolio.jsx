/**
 * Portfolio page — summary bar, positions table, trade panel.
 */

import { useEffect } from 'react'
import { usePortfolioStore } from '../store/usePortfolioStore'
import PortfolioBar    from '../components/portfolio/PortfolioBar'
import PortfolioChart  from '../components/portfolio/PortfolioChart'
import PositionsTable  from '../components/portfolio/PositionsTable'
import TradePanel      from '../components/portfolio/TradePanel'
import { PageLoader }  from '../components/common/Loader'

export default function Portfolio() {
  const fetch     = usePortfolioStore((s) => s.fetch)
  const portfolio = usePortfolioStore((s) => s.portfolio)
  const loading   = usePortfolioStore((s) => s.loading)
  const error     = usePortfolioStore((s) => s.error)

  // Fetch on mount, then auto-refresh every 30s to keep realized P&L current
  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 30_000)
    return () => clearInterval(id)
  }, [fetch])

  return (
    <div className="px-5 py-5 space-y-5 max-w-screen-2xl mx-auto w-full">
      {/* Summary bar */}
      <PortfolioBar portfolio={portfolio} loading={loading} />

      {error && (
        <div className="px-4 py-3 bg-red-900/40 border border-red-800 rounded text-sm text-red-300">
          {error}
          <button
            onClick={fetch}
            className="ml-3 underline hover:no-underline text-red-400 text-xs"
          >
            Retry
          </button>
        </div>
      )}

      {loading && !portfolio && <PageLoader message="Loading portfolio…" />}

      {/* Equity chart — visible once we have ≥2 snapshots */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3">
        <PortfolioChart />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Positions — takes 2/3 */}
        <div className="lg:col-span-2 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-widest">
              Open Positions
            </h2>
            <button
              onClick={fetch}
              className="text-xs text-gray-600 hover:text-gray-400 transition"
            >
              ↺ Refresh
            </button>
          </div>
          <PositionsTable positions={portfolio?.positions || []} />
        </div>

        {/* Trade panel — takes 1/3 */}
        <div>
          <TradePanel />
        </div>
      </div>
    </div>
  )
}
