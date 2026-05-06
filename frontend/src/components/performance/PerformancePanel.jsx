/**
 * PerformancePanel — summary metrics cards + derived insights.
 *
 * Metrics (from summary API):
 *   win_rate, expectancy, avg_win, avg_loss, avg_mfe, avg_mae,
 *   avg_return_per_trade, avg_return_per_hour, total_closed, total_open.
 *
 * Insights (derived from history in-store — no backend calls):
 *   1. Best signal type by win rate
 *   2. Worst drawdown trade (lowest mae_pct)
 *   3. Avg hold time — winners vs losers
 *   4. Confidence vs outcome (shown only when data contains confidence field)
 */

import { useMemo } from 'react'
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

/** P&L % from the signal's perspective. */
function tradePnl(t) {
  if (!t.entry_price || !t.exit_price) return null
  return t.signal === 'BUY'
    ? (t.exit_price - t.entry_price) / t.entry_price * 100
    : (t.entry_price - t.exit_price) / t.entry_price * 100
}

function isWin(outcome) {
  return outcome === 'STRONG_WIN' || outcome === 'WEAK_WIN'
}

/** Format minutes as "45m" or "1h 30m". */
function fmtMins(mins) {
  if (mins == null || isNaN(mins)) return '—'
  const m = Math.round(Number(mins))
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

// ── insight computation (pure — memoised by caller) ───────────────────────────

function computeInsights(history) {
  if (!history || history.length === 0) return null

  // 1. Best signal type by win rate (min 2 trades for statistical relevance)
  const bySignal = {}
  for (const t of history) {
    const s = t.signal || 'UNKNOWN'
    if (!bySignal[s]) bySignal[s] = { wins: 0, total: 0, pnlSum: 0, pnlN: 0 }
    bySignal[s].total++
    if (isWin(t.outcome)) bySignal[s].wins++
    const p = tradePnl(t)
    if (p != null) { bySignal[s].pnlSum += p; bySignal[s].pnlN++ }
  }
  let bestSig = null
  let bestWr  = -1
  for (const [sig, stats] of Object.entries(bySignal)) {
    if (stats.total < 2) continue
    const wr = stats.wins / stats.total * 100
    if (wr > bestWr) {
      bestWr  = wr
      bestSig = {
        sig,
        wr,
        n:      stats.total,
        avgPnl: stats.pnlN > 0 ? stats.pnlSum / stats.pnlN : null,
      }
    }
  }

  // 2. Worst drawdown trade — lowest (most negative) mae_pct
  let worstDD = null
  for (const t of history) {
    if (t.mae_pct == null) continue
    if (!worstDD || t.mae_pct < worstDD.mae_pct) worstDD = t
  }

  // 3. Avg hold time — winners vs losers
  const winDurs  = []
  const lossDurs = []
  for (const t of history) {
    if (t.duration_minutes == null) continue
    if (isWin(t.outcome))         winDurs.push(t.duration_minutes)
    else if (t.outcome === 'LOSS') lossDurs.push(t.duration_minutes)
  }
  const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null
  const avgWinDur  = avg(winDurs)
  const avgLossDur = avg(lossDurs)

  // 4. Confidence vs outcome
  // ForwardTrade doesn't store confidence — rendered as a placeholder.
  // If future backend adds the field, the bucketing below activates automatically.
  const confTrades = history.filter((t) => t.confidence != null)
  let confRows = null
  if (confTrades.length > 0) {
    const tiers = {
      'High (≥0.7)': { wins: 0, total: 0 },
      'Mid (0.5–0.7)': { wins: 0, total: 0 },
      'Low (<0.5)':  { wins: 0, total: 0 },
    }
    for (const t of confTrades) {
      const tier = t.confidence >= 0.7 ? 'High (≥0.7)' : t.confidence >= 0.5 ? 'Mid (0.5–0.7)' : 'Low (<0.5)'
      tiers[tier].total++
      if (isWin(t.outcome)) tiers[tier].wins++
    }
    confRows = Object.entries(tiers)
      .filter(([, s]) => s.total > 0)
      .map(([tier, s]) => ({ tier, wr: (s.wins / s.total * 100).toFixed(0), n: s.total }))
  }

  return {
    bestSig,
    worstDD,
    avgWinDur,
    avgLossDur,
    winCount:  winDurs.length,
    lossCount: lossDurs.length,
    confRows,
    n: history.length,
  }
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

/** Insight card — slightly smaller value text to fit longer strings. */
function InsightCard({ label, children, className }) {
  return (
    <div className={clsx(
      'bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex flex-col gap-1.5',
      className,
    )}>
      <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">{label}</span>
      {children}
    </div>
  )
}

// ── insight sub-panels ────────────────────────────────────────────────────────

function BestSignalInsight({ bestSig }) {
  if (!bestSig) {
    return <span className="text-xs text-gray-600">Need ≥ 2 trades per signal type.</span>
  }
  const sigColor = {
    BUY: 'text-green-400', SELL: 'text-orange-400', FORCE_SELL: 'text-red-400',
  }[bestSig.sig] || 'text-gray-300'

  return (
    <>
      <div className="flex items-baseline gap-2">
        <span className={clsx('text-base font-black font-mono', sigColor)}>{bestSig.sig}</span>
        <span className="text-sm font-bold text-green-400">{bestSig.wr.toFixed(0)}% WR</span>
      </div>
      <span className="text-[10px] text-gray-600">
        {bestSig.n} trade{bestSig.n !== 1 ? 's' : ''}
        {bestSig.avgPnl != null && (
          <> · avg {bestSig.avgPnl >= 0 ? '+' : ''}{bestSig.avgPnl.toFixed(2)}%</>
        )}
      </span>
    </>
  )
}

function WorstDrawdownInsight({ worstDD }) {
  if (!worstDD) {
    return <span className="text-xs text-gray-600">No MAE data yet.</span>
  }
  const mae = Number(worstDD.mae_pct)
  return (
    <>
      <div className="flex items-baseline gap-2">
        <span className="text-base font-black font-mono text-red-400">
          {mae >= 0 ? '+' : ''}{mae.toFixed(2)}%
        </span>
        <span className="text-xs text-gray-500">{worstDD.symbol}</span>
      </div>
      <span className="text-[10px] text-gray-600">
        {worstDD.signal} · {worstDD.outcome?.replace('_', ' ') ?? '—'}
      </span>
    </>
  )
}

function HoldTimeInsight({ avgWinDur, avgLossDur, winCount, lossCount }) {
  if (avgWinDur == null && avgLossDur == null) {
    return <span className="text-xs text-gray-600">No hold-time data yet.</span>
  }

  // Interpret: if losers held longer → quick-exit strategy works
  let note = null
  if (avgWinDur != null && avgLossDur != null) {
    const diff = avgLossDur - avgWinDur
    if (Math.abs(diff) > 5) {
      note = diff > 0
        ? 'Losers held longer — cut early'
        : 'Winners held longer — let them run'
    }
  }

  return (
    <>
      <div className="flex gap-4">
        {avgWinDur != null && (
          <div>
            <span className="text-[10px] text-gray-600">Wins</span>
            <div className="text-sm font-bold font-mono text-green-400">{fmtMins(avgWinDur)}</div>
            <span className="text-[10px] text-gray-700">{winCount} trades</span>
          </div>
        )}
        {avgLossDur != null && (
          <div>
            <span className="text-[10px] text-gray-600">Losses</span>
            <div className="text-sm font-bold font-mono text-red-400">{fmtMins(avgLossDur)}</div>
            <span className="text-[10px] text-gray-700">{lossCount} trades</span>
          </div>
        )}
      </div>
      {note && <span className="text-[10px] text-yellow-600">{note}</span>}
    </>
  )
}

function ConfidenceInsight({ confRows }) {
  if (!confRows) {
    return (
      <span className="text-xs text-gray-700 italic">
        Confidence not tracked in forward trades.
      </span>
    )
  }
  return (
    <div className="flex flex-col gap-0.5">
      {confRows.map(({ tier, wr, n }) => (
        <div key={tier} className="flex items-center gap-2 text-xs">
          <span className="text-gray-500 w-24 shrink-0">{tier}</span>
          <span className={clsx(
            'font-mono font-bold',
            Number(wr) >= 50 ? 'text-green-400' : Number(wr) >= 35 ? 'text-yellow-400' : 'text-red-400',
          )}>
            {wr}%
          </span>
          <span className="text-gray-700 text-[10px]">({n})</span>
        </div>
      ))}
    </div>
  )
}

// ── main component ────────────────────────────────────────────────────────────

export default function PerformancePanel() {
  const summary = usePerformanceStore((s) => s.summary)
  const history = usePerformanceStore((s) => s.history)
  const loading = usePerformanceStore((s) => s.loading)

  const insights = useMemo(() => computeInsights(history), [history])

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
    <div className="space-y-4">
      {/* ── Metric cards ── */}
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

      {/* ── Insights ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <InsightCard label="Best Signal Type">
          <BestSignalInsight bestSig={insights?.bestSig ?? null} />
        </InsightCard>

        <InsightCard label="Worst Drawdown">
          <WorstDrawdownInsight worstDD={insights?.worstDD ?? null} />
        </InsightCard>

        <InsightCard label="Hold Time — Win vs Loss">
          <HoldTimeInsight
            avgWinDur={insights?.avgWinDur ?? null}
            avgLossDur={insights?.avgLossDur ?? null}
            winCount={insights?.winCount ?? 0}
            lossCount={insights?.lossCount ?? 0}
          />
        </InsightCard>

        <InsightCard label="Confidence vs Outcome">
          <ConfidenceInsight confRows={insights?.confRows ?? null} />
        </InsightCard>
      </div>

      {/* Sample-size caveat */}
      {insights && (
        <p className="text-[10px] text-gray-700">
          Insights derived from {insights.n} most-recently loaded trade{insights.n !== 1 ? 's' : ''}.
        </p>
      )}
    </div>
  )
}
