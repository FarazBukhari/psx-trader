/**
 * Sparkline — mini inline price chart using Recharts.
 * Accepts a `data` array of numbers (prices) or {value} objects.
 * If data is empty/null, renders nothing.
 */

import { LineChart, Line, ResponsiveContainer } from 'recharts'

export default function Sparkline({ data = [], color = '#22c55e', width = 60, height = 24 }) {
  if (!data || data.length < 2) return <span className="text-gray-700 text-xs">—</span>

  const points = data.map((d, i) => ({
    i,
    v: typeof d === 'object' ? d.value ?? d.v ?? 0 : d,
  }))

  const first = points[0].v
  const last  = points[points.length - 1].v
  const lineColor = last >= first ? '#22c55e' : '#f97316'

  return (
    <ResponsiveContainer width={width} height={height}>
      <LineChart data={points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <Line
          type="monotone"
          dataKey="v"
          stroke={color || lineColor}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
