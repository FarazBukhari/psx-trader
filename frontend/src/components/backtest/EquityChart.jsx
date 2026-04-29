/**
 * EquityChart — equity curve over time using Recharts.
 * Accepts equity_curve from backend: array of [unix_ts, value] tuples.
 */

import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'

function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString('en-PK', { month: 'short', day: 'numeric' })
}

function fmtCcy(v) {
  return `PKR ${Number(v).toLocaleString('en-PK', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

export default function EquityChart({ equityCurve = [], startingCash = null, label = '' }) {
  if (!equityCurve || equityCurve.length < 2) {
    return (
      <div className="flex items-center justify-center h-28 text-gray-600 text-xs">
        No equity curve data.
      </div>
    )
  }

  const data = equityCurve.map(([ts, val]) => ({ ts, val }))
  const first = data[0].val
  const last  = data[data.length - 1].val
  const isUp  = last >= first
  const lineColor = isUp ? '#22c55e' : '#f97316'

  return (
    <div>
      {label && <div className="text-xs text-gray-500 mb-1">{label}</div>}
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 4, left: 8 }}>
          <XAxis
            dataKey="ts"
            tickFormatter={fmtDate}
            tick={{ fill: '#6b7280', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={(v) => `${(v / 1000).toFixed(0)}K`}
            tick={{ fill: '#6b7280', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={44}
          />
          {startingCash != null && (
            <ReferenceLine y={startingCash} stroke="#374151" strokeDasharray="4 2" />
          )}
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 6 }}
            labelStyle={{ color: '#9ca3af', fontSize: 10 }}
            formatter={(v) => [fmtCcy(v), 'Equity']}
            labelFormatter={fmtDate}
          />
          <Line
            type="monotone"
            dataKey="val"
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
