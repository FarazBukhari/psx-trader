/**
 * PerformanceChart — cumulative P&L equity curve built from closed trade history.
 *
 * Sorts trades by exit_time, then compounds each trade's return
 * against a 100-base index.  Shows STRONG_WIN / WEAK_WIN / LOSS dots
 * on the curve so you can see where each trade landed.
 */

import { useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, Dot,
} from 'recharts'
import clsx from 'clsx'
import { usePerformanceStore } from '../../store/usePerformanceStore'

// ── helpers ───────────────────────────────────────────────────────────────────

function pnlPct(trade) {
  const { signal, entry_price: ep, exit_price: xp } = trade
  if (!ep || !xp) return 0
  return signal === 'BUY'
    ? (xp - ep) / ep * 100
    : (ep - xp) / ep * 100
}

function fmtDate(unix) {
  if (!unix) return ''
  return new Date(unix * 1000).toLocaleDateString('en-PK', {
    month: 'short', day: 'numeric',
  })
}

function fmtDateTime(unix) {
  if (!unix) return ''
  return new Date(unix * 1000).toLocaleString('en-PK', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const OUTCOME_DOT_COLOR = {
  STRONG_WIN: '#22c55e',
  WEAK_WIN:   '#4ade80',
  LOSS:       '#ef4444',
  BREAKEVEN:  '#eab308',
}

// ── custom dot — coloured by outcome ─────────────────────────────────────────

function OutcomeDot(props) {
  const { cx, cy, payload } = props
  if (!payload?.outcome) return null
  const fill = OUTCOME_DOT_COLOR[payload.outcome] || '#6b7280'
  return <circle cx={cx} cy={cy} r={3} fill={fill} stroke="none" />
}

// ── custom tooltip ────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.[0]) return null
  const d = payload[0].payload
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">{fmtDateTime(d.exit_time)}</div>
      {d.symbol && (
        <div className="text-white font-bold">{d.symbol} <span className="text-gray-500 font-normal">({d.signal})</span></div>
      )}
      <div className={clsx(
        'font-mono font-bold mt-0.5',
        d.tradePnl >= 0 ? 'text-green-400' : 'text-red-400',
      )}>
        Trade: {d.tradePnl >= 0 ? '+' : ''}{d.tradePnl?.toFixed(2)}%
      </div>
      <div className="text-gray-300 font-mono">
        Index: {d.index?.toFixed(2)}
      </div>
      {d.outcome && (
        <div className="mt-0.5" style={{ color: OUTCOME_DOT_COLOR[d.outcome] || '#6b7280' }}>
          {d.outcome.replace('_', ' ')}
        </div>
      )}
    </div>
  )
}

// ── main ──────────────────────────────────────────────────────────────────────

export default function PerformanceChart() {
  const history = usePerformanceStore((s) => s.history)

  const { data, isUp } = useMemo(() => {
    if (!history || history.length === 0) return { data: [], isUp: true }

    // Sort by exit_time ascending
    const sorted = [...history]
      .filter((t) => t.exit_time)
      .sort((a, b) => a.exit_time - b.exit_time)

    // Build cumulative index starting at 100
    let index = 100
    const points = sorted.map((t) => {
      const pnl = pnlPct(t)
      index = index * (1 + pnl / 100)
      return {
        exit_time: t.exit_time,
        symbol:    t.symbol,
        signal:    t.signal,
        outcome:   t.outcome,
        tradePnl:  pnl,
        index:     parseFloat(index.toFixed(4)),
      }
    })

    // Prepend baseline
    const baseline = { exit_time: sorted[0]?.exit_time - 1, index: 100, outcome: null }
    const full = [baseline, ...points]
    const last = full[full.length - 1]?.index ?? 100

    return { data: full, isUp: last >= 100 }
  }, [history])

  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-40 text-xs text-gray-600">
        Equity curve will appear after the first closed trade.
      </div>
    )
  }

  const lineColor = isUp ? '#22c55e' : '#f97316'
  const minVal    = Math.min(...data.map((d) => d.index)) * 0.998
  const maxVal    = Math.max(...data.map((d) => d.index)) * 1.002

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
        <XAxis
          dataKey="exit_time"
          tickFormatter={fmtDate}
          tick={{ fill: '#6b7280', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[minVal, maxVal]}
          tickFormatter={(v) => v.toFixed(1)}
          tick={{ fill: '#6b7280', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={40}
        />
        <ReferenceLine y={100} stroke="#374151" strokeDasharray="4 2" />
        <Tooltip content={<CustomTooltip />} />
        <Line
          type="monotone"
          dataKey="index"
          stroke={lineColor}
          strokeWidth={2}
          dot={<OutcomeDot />}
          activeDot={{ r: 5, fill: lineColor }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
