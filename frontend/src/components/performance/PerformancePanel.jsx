/**
 * PerformancePanel — summary metrics cards.
 *
 * Displays: win_rate, expectancy, avg_win, avg_loss,
 *           avg_mfe, avg_mae, avg_return_per_trade,
 *           avg_return_per_hour, total_closed, total_open.
 */

import clsx from 'clsx'
import { usePerformanceStore } from '../../store/usePerformanceStore'

// ── helpers ──────────────────────────────────────────────────────────────────

function pct(v, decimals = 2) {
  if (v == null || isNaN(v)) return '—'
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(decimals)}%`
}

function colorPct(v) {
  if (v == null) return 'text-gray-400'
  return Number(v) >= 0 ? 'text-green-400' : 'text-red-400'
}

// ── sub-components ────────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, valueClass = 'text-white' }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">{label}</span>
      <span className={clsx('text-xl font-black font-mono leading-tight', valueClass)}>{value}</span>
      {sub && <span className="text-[10px] text-gray-600">{sub}</span>}
    </div>
  )
}

function Divider() {
  return <div className="hidden lg:block w-px bg-gray-800 self-stretch mx-1" />
}

// ── main component ────────────────────────────────────────────────────────────

export default function PerformancePanel() {
  const summary = usePerformanceStore((s) => s.summary)
  const loading = usePerformanceStore((s) => s.loading)

  if (!summary && loading) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4 text-xs text-gray-600 animate-pulse">
        Loading performance metrics…
      </div>
    )
  }

  if (!summary) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4 text-xs text-gray-600">
        No performance data yet — trades will appear once signals fire.
      </div>
    )
  }

  const {
    win_rate, expectancy,
    avg_win_pct, avg_loss_pct,
    avg_mfe, avg_mae,
    avg_return_per_trade, avg_return_per_hour,
    total_closed, total_open,
  } = summary

  const winColor = win_rate >= 50 ? 'text-green-400' : win_rate >= 35 ? 'text-yellow-400' : 'text-red-400'

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-10 gap-3">
      <MetricCard
        label="Win Rate"
        value={win_rate != null ? `${Number(win_rate).toFixed(1)}%` : '—'}
        sub={`${total_closed ?? 0} closed trades`}
        valueClass={winColor}
      />
      <MetricCard
        label="Expectancy"
        value={pct(expectancy)}
        sub="per trade"
        valueClass={colorPct(expectancy)}
      />
      <MetricCard
        label="Avg Win"
        value={pct(avg_win_pct)}
        sub="on winning trades"
        valueClass="text-green-400"
      />
      <MetricCard
        label="Avg Loss"
        value={pct(avg_loss_pct)}
        sub="on losing trades"
        valueClass="text-red-400"
      />
      <MetricCard
        label="Avg MFE"
        value={pct(avg_mfe)}
        sub="max favourable"
        valueClass="text-blue-400"
      />
      <MetricCard
        label="Avg MAE"
        value={pct(avg_mae)}
        sub="max adverse"
        valueClass="text-orange-400"
      />
      <MetricCard
        label="Return / Trade"
        value={pct(avg_return_per_trade)}
        sub="all closed"
        valueClass={colorPct(avg_return_per_trade)}
      />
      <MetricCard
        label="Return / Hour"
        value={pct(avg_return_per_hour)}
        sub="time-normalised"
        valueClass={colorPct(avg_return_per_hour)}
      />
      <MetricCard
        label="Closed"
        value={total_closed ?? 0}
        sub="evaluated trades"
        valueClass="text-gray-300"
      />
      <MetricCard
        label="Open"
        value={total_open ?? 0}
        sub="tracking now"
        valueClass={total_open > 0 ? 'text-yellow-400' : 'text-gray-500'}
      />
    </div>
  )
}
