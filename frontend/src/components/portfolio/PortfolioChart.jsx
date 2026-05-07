/**
 * PortfolioChart — in-session equity curve (total portfolio value over time).
 *
 * X-axis is always anchored at 09:30 PKT and extends to the current time,
 * so the chart shows the full trading day even before the first data point
 * arrives. Data points are added on every WebSocket tick.
 */

import { useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { usePortfolioStore, todaySessionStartMs } from '../../store/usePortfolioStore'

// PSX market closes at 15:30 PKT
function todaySessionEndMs() {
  const d = new Date()
  d.setHours(15, 30, 0, 0)
  return d.getTime()
}

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' })
}

function fmtCcy(v) {
  return `PKR ${Number(v).toLocaleString('en-PK', { maximumFractionDigits: 0 })}`
}

export default function PortfolioChart() {
  const history = usePortfolioStore((s) => s.valueHistory)

  // Drop zero-value points (before positions were added) and clamp to today's session.
  const sessionStart = todaySessionStartMs()
  const sessionEnd   = todaySessionEndMs()

  const trimmed = useMemo(
    () => history.filter((h) => h.value > 0 && h.ts >= sessionStart && h.ts <= sessionEnd),
    [history, sessionStart, sessionEnd],
  )

  // X-axis runs 09:30 → 15:30. After close, cap the right edge at 15:30 so
  // the chart doesn't extend into post-market with a misleading flat line.
  // We inject a phantom point at 09:30 so Recharts draws the axis from there.
  const now        = Date.now()
  const xDomainEnd = Math.min(now, sessionEnd)

  const chartData = useMemo(() => {
    if (!trimmed.length) return []
    const firstReal = trimmed[0]
    // Only prepend phantom if the first real point is more than 1 min after open
    if (firstReal.ts - sessionStart > 60_000) {
      return [{ ts: sessionStart, value: firstReal.value }, ...trimmed]
    }
    return trimmed
  }, [trimmed, sessionStart])

  const baseline  = trimmed.length ? trimmed[0].value : null
  const last      = trimmed.length ? trimmed[trimmed.length - 1].value : null

  const isUp      = last != null ? last >= baseline : true
  const lineColor = isUp ? '#22c55e' : '#f97316'
  const pctChange = (baseline && last != null)
    ? ((last - baseline) / baseline * 100).toFixed(2)
    : null
  const sign = isUp ? '+' : ''

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-gray-600 uppercase tracking-wider">
          Portfolio Value — Today's Session
        </span>
        {pctChange != null ? (
          <span className={`text-xs font-mono font-semibold ${isUp ? 'text-green-400' : 'text-red-400'}`}>
            {sign}{pctChange}% since open
          </span>
        ) : (
          <span className="text-[10px] text-gray-600">awaiting first tick…</span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={100}>
        <LineChart
          data={chartData}
          margin={{ top: 4, right: 8, bottom: 0, left: 8 }}
        >
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={[sessionStart, xDomainEnd]}
            ticks={(() => {
              // Fixed 30-min ticks: 09:30, 10:00, 10:30 … 15:30
              const t = []
              for (let m = 0; m <= 360; m += 30) {
                t.push(sessionStart + m * 60 * 1000)
              }
              return t
            })()}
            tickFormatter={fmtTime}
            tick={{ fill: '#6b7280', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(v) => `${(v / 1000).toFixed(1)}K`}
            tick={{ fill: '#6b7280', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            width={40}
            domain={['auto', 'auto']}
          />
          {baseline != null && (
            <ReferenceLine y={baseline} stroke="#374151" strokeDasharray="3 2" />
          )}
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
            labelFormatter={fmtTime}
            formatter={(v) => [fmtCcy(v), 'Portfolio Value']}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke={lineColor}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
