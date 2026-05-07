/**
 * PerformanceChart — cumulative P&L equity curve built from closed trade history.
 *
 * Sorts trades by exit_time, then compounds each trade's return
 * against a 100-base index.  Shows STRONG_WIN / WEAK_WIN / LOSS dots
 * on the curve so you can see where each trade landed.
 */

import { useMemo, useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import clsx from 'clsx'
import { usePerformanceStore } from '../../store/usePerformanceStore'
import { getBenchmark } from '../../api/performance'

// ── helpers ───────────────────────────────────────────────────────────────────

function pnlPct(trade) {
  const { signal, entry_price: ep, exit_price: xp } = trade
  if (!ep || !xp) return 0
  return signal === 'BUY'
    ? (xp - ep) / ep * 100
    : (ep - xp) / ep * 100
}

/**
 * Smart date formatter: if all trades are within a single calendar day,
 * show HH:MM instead of "7 May" to avoid repeated identical labels.
 */
function makeFmtDate(isSameDay) {
  return function fmtDate(unix) {
    if (!unix) return ''
    const d = new Date(unix * 1000)
    if (isSameDay) {
      return d.toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleDateString('en-PK', { month: 'short', day: 'numeric' })
  }
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

function CustomTooltip({ active, payload, proxyLabel }) {
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
        Portfolio: {d.index?.toFixed(2)}
      </div>
      {d.kse_proxy != null && (
        <div className="text-gray-500 font-mono">
          {proxyLabel}: {d.kse_proxy.toFixed(2)}
        </div>
      )}
      {d.outcome && (
        <div className="mt-0.5" style={{ color: OUTCOME_DOT_COLOR[d.outcome] || '#6b7280' }}>
          {d.outcome.replace('_', ' ')}
        </div>
      )}
    </div>
  )
}

// ── benchmark helpers ─────────────────────────────────────────────────────────

/** Binary-search nearest benchmark value for a given timestamp. */
function nearestBenchmark(points, time) {
  if (!points.length) return null
  let lo = 0, hi = points.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (points[mid].time < time) lo = mid + 1
    else hi = mid
  }
  if (lo === 0) return points[0].value
  const before = points[lo - 1]
  const after  = points[lo]
  return Math.abs(before.time - time) <= Math.abs(after.time - time)
    ? before.value
    : after.value
}

// ── main ──────────────────────────────────────────────────────────────────────

const PROXY_SYMBOL  = 'OGDC'
const PROXY_COLOR   = '#6b7280'

export default function PerformanceChart() {
  const history = usePerformanceStore((s) => s.history)

  // Benchmark is opt-in — off by default to avoid confusion with a random stock
  const [showBenchmark, setShowBenchmark] = useState(false)
  const [benchPoints,   setBenchPoints]   = useState([])
  const [proxyLabel,    setProxyLabel]    = useState(PROXY_SYMBOL)
  const [benchLoading,  setBenchLoading]  = useState(false)

  // ── equity curve ──────────────────────────────────────────────────────────
  const { equityCurve, timeRange, isSameDay, tradeCount } = useMemo(() => {
    if (!history || history.length === 0) {
      return { equityCurve: [], timeRange: null, isSameDay: false, tradeCount: 0 }
    }

    const sorted = [...history]
      .filter((t) => t.exit_time)
      .sort((a, b) => a.exit_time - b.exit_time)

    if (!sorted.length) {
      return { equityCurve: [], timeRange: null, isSameDay: false, tradeCount: 0 }
    }

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

    const firstTs = sorted[0].exit_time
    const lastTs  = sorted[sorted.length - 1].exit_time
    const sameDay = (lastTs - firstTs) < 24 * 3600
      && new Date(firstTs * 1000).toDateString() === new Date(lastTs * 1000).toDateString()

    return {
      equityCurve: [{ exit_time: firstTs - 1, index: 100, outcome: null }, ...points],
      timeRange:   { since: firstTs, until: lastTs },
      isSameDay:   sameDay,
      tradeCount:  sorted.length,
    }
  }, [history])

  // ── fetch benchmark only when user explicitly enables it ──────────────────
  useEffect(() => {
    if (!showBenchmark || !timeRange) return
    let cancelled = false
    setBenchLoading(true)
    getBenchmark(timeRange.since, timeRange.until, PROXY_SYMBOL)
      .then((res) => {
        if (cancelled) return
        setBenchPoints(res.points ?? [])
        setProxyLabel(res.proxy ?? PROXY_SYMBOL)
      })
      .catch(() => { if (!cancelled) setBenchPoints([]) })
      .finally(() => { if (!cancelled) setBenchLoading(false) })
    return () => { cancelled = true }
  }, [showBenchmark, timeRange?.since, timeRange?.until])

  // Clear benchmark data when toggled off
  useEffect(() => {
    if (!showBenchmark) setBenchPoints([])
  }, [showBenchmark])

  // ── merge benchmark into curve data ──────────────────────────────────────
  const data = useMemo(() => {
    if (!equityCurve.length) return []
    if (!showBenchmark || !benchPoints.length) return equityCurve
    return equityCurve.map((pt) => ({
      ...pt,
      kse_proxy: nearestBenchmark(benchPoints, pt.exit_time),
    }))
  }, [equityCurve, benchPoints, showBenchmark])

  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-40 text-xs text-gray-600">
        Equity curve will appear after the first closed trade.
      </div>
    )
  }

  const last      = data[data.length - 1]
  const finalIdx  = last?.index ?? 100
  const isUp      = finalIdx >= 100
  const lineColor = isUp ? '#22c55e' : '#f97316'
  const totalPnl  = (finalIdx - 100).toFixed(2)
  const sign      = isUp ? '+' : ''

  const hasBenchmark = showBenchmark && benchPoints.length > 0
  const allValues    = data.flatMap((d) => [d.index, hasBenchmark ? d.kse_proxy : null].filter(Boolean))
  const minVal       = Math.min(...allValues) * 0.998
  const maxVal       = Math.max(...allValues) * 1.002

  return (
    <div className="space-y-2">
      {/* Legend + controls row */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-4 text-[10px] text-gray-500">
          <span className="flex items-center gap-1">
            <span className="inline-block w-4 h-0.5 rounded" style={{ background: lineColor }} />
            <span className="text-gray-400 font-medium">Portfolio</span>
            <span className={isUp ? 'text-green-400' : 'text-red-400'}>
              {sign}{totalPnl}%
            </span>
            <span className="text-gray-700">({tradeCount} trades)</span>
          </span>
          {hasBenchmark && (
            <span className="flex items-center gap-1">
              <span className="inline-block w-4 h-0.5 rounded" style={{ background: PROXY_COLOR, opacity: 0.7 }} />
              <span>{proxyLabel}</span>
              <span className="text-gray-700 italic">price proxy, not KSE-100</span>
            </span>
          )}
        </div>

        {/* Benchmark toggle */}
        <button
          onClick={() => setShowBenchmark((v) => !v)}
          className={`text-[10px] px-2 py-0.5 rounded border transition ${
            showBenchmark
              ? 'border-gray-600 text-gray-400 bg-gray-800'
              : 'border-gray-800 text-gray-600 hover:text-gray-400 hover:border-gray-700'
          }`}
          title={`${showBenchmark ? 'Hide' : 'Show'} ${PROXY_SYMBOL} price as a rough market proxy`}
        >
          {benchLoading ? '…' : showBenchmark ? `Hide ${proxyLabel}` : `vs ${PROXY_SYMBOL}`}
        </button>
      </div>

      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <XAxis
            dataKey="exit_time"
            tickFormatter={makeFmtDate(isSameDay)}
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
          <Tooltip content={<CustomTooltip proxyLabel={proxyLabel} />} />

          {hasBenchmark && (
            <Line
              type="monotone"
              dataKey="kse_proxy"
              stroke={PROXY_COLOR}
              strokeWidth={1.5}
              strokeDasharray="5 3"
              strokeOpacity={0.7}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          )}

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
    </div>
  )
}
