/**
 * PortfolioChart — in-session equity curve (total portfolio value over time).
 * Data is accumulated in usePortfolioStore.valueHistory every time the
 * portfolio is fetched or updated.
 */

import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { usePortfolioStore } from '../../store/usePortfolioStore'

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' })
}

function fmtCcy(v) {
  return `PKR ${Number(v).toLocaleString('en-PK', { maximumFractionDigits: 0 })}`
}

export default function PortfolioChart() {
  const history = usePortfolioStore((s) => s.valueHistory)

  if (history.length < 2) {
    return (
      <div className="flex items-center justify-center h-20 text-gray-700 text-xs">
        Equity chart builds as you use the app — refresh portfolio to add data points.
      </div>
    )
  }

  const baseline = history[0].value
  const last     = history[history.length - 1].value
  const isUp     = last >= baseline
  const lineColor = isUp ? '#22c55e' : '#f97316'

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-gray-600 uppercase tracking-wider">
          Session Equity Curve
        </span>
        <span className={`text-xs font-mono font-semibold ${isUp ? 'text-green-400' : 'text-red-400'}`}>
          {isUp ? '+' : ''}{((last - baseline) / baseline * 100).toFixed(2)}% session
        </span>
      </div>
      <ResponsiveContainer width="100%" height={100}>
        <LineChart data={history} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <XAxis
            dataKey="ts"
            tickFormatter={fmtTime}
            tick={{ fill: '#6b7280', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={(v) => `${(v / 1000).toFixed(0)}K`}
            tick={{ fill: '#6b7280', fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            width={36}
            domain={['auto', 'auto']}
          />
          <ReferenceLine y={baseline} stroke="#374151" strokeDasharray="3 2" />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 6, fontSize: 11 }}
            labelFormatter={fmtTime}
            formatter={(v) => [fmtCcy(v), 'Portfolio']}
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
