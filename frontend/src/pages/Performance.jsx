/**
 * Performance page — forward-testing engine dashboard.
 *
 * Layout:
 *   PerformancePanel   — 10-metric summary cards
 *   PerformanceChart   — cumulative equity curve (recharts)
 *   LiveTradesTable    — OPEN trades updating every 10 s
 *   TradeHistoryTable  — CLOSED trades, sortable + paginated
 *
 * Auto-refresh: every 10 s (Step 6), staggered so live updates first.
 */

import { useEffect, useRef } from 'react'
import { usePerformanceStore } from '../store/usePerformanceStore'
import PerformancePanel   from '../components/performance/PerformancePanel'
import PerformanceChart   from '../components/performance/PerformanceChart'
import LiveTradesTable    from '../components/performance/LiveTradesTable'
import TradeHistoryTable  from '../components/performance/TradeHistoryTable'
import Loader             from '../components/common/Loader'

const REFRESH_MS = 10_000   // Step 6

function SectionHeader({ title, sub }) {
  return (
    <div className="flex items-baseline gap-3">
      <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">{title}</h3>
      {sub && <span className="text-xs text-gray-700">{sub}</span>}
    </div>
  )
}

export default function Performance() {
  const fetchAll  = usePerformanceStore((s) => s.fetchAll)
  const fetchLive = usePerformanceStore((s) => s.fetchLive)
  const loading   = usePerformanceStore((s) => s.loading)
  const error     = usePerformanceStore((s) => s.error)
  const clearError= usePerformanceStore((s) => s.clearError)

  // Initial full fetch on mount
  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  // Auto-refresh every 10 s
  // Live trades update every tick; summary + history every other tick
  const tickRef = useRef(0)
  useEffect(() => {
    const id = setInterval(() => {
      tickRef.current += 1
      if (tickRef.current % 2 === 0) {
        fetchAll()
      } else {
        fetchLive()
      }
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchAll, fetchLive])

  return (
    <div className="px-5 py-5 max-w-screen-xl mx-auto w-full space-y-6">

      {/* ── Page title ── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-bold text-gray-300 uppercase tracking-widest">
            Forward Testing Performance
          </h2>
          <p className="text-xs text-gray-600 mt-0.5">
            Live signal tracking — not backtesting. Refreshes every {REFRESH_MS / 1000}s.
          </p>
        </div>
        {loading && <Loader size="sm" message="Refreshing…" />}
      </div>

      {/* ── Error banner ── */}
      {error && (
        <div className="flex items-center gap-3 bg-red-950/50 border border-red-900 rounded-lg px-4 py-2.5 text-xs text-red-300">
          <span>⚠ {error}</span>
          <button
            onClick={clearError}
            className="ml-auto text-red-500 hover:text-red-300 transition-colors"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Summary metrics ── */}
      <section className="space-y-2">
        <SectionHeader title="Summary" sub="aggregate across all closed trades" />
        <PerformancePanel />
      </section>

      {/* ── Equity curve ── */}
      <section className="space-y-2">
        <SectionHeader title="Equity Curve" sub="cumulative return index (base 100)" />
        <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3">
          <PerformanceChart />
        </div>
      </section>

      {/* ── Open trades ── */}
      <section className="space-y-2">
        <SectionHeader title="Live Tracking" sub="OPEN trades — updating every 10 s" />
        <LiveTradesTable />
      </section>

      {/* ── Closed trades ── */}
      <section className="space-y-2">
        <SectionHeader title="Trade History" sub="closed trades — sortable" />
        <TradeHistoryTable />
      </section>

    </div>
  )
}
