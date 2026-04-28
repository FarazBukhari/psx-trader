/**
 * SummaryCards — top-level signal count cards.
 */

export default function SummaryCards({ signals }) {
  const counts = signals.reduce((acc, s) => {
    acc[s.signal] = (acc[s.signal] || 0) + 1
    return acc
  }, {})

  const cards = [
    { label: 'Total',      value: signals.length,          color: 'border-gray-700 text-gray-200',  bg: 'bg-gray-900' },
    { label: 'BUY',        value: counts.BUY || 0,         color: 'border-green-700 text-green-300', bg: 'bg-green-900/20' },
    { label: 'SELL',       value: counts.SELL || 0,        color: 'border-red-700 text-red-300',     bg: 'bg-red-900/20' },
    { label: 'FORCE SELL', value: counts.FORCE_SELL || 0,  color: 'border-red-500 text-red-200',     bg: 'bg-red-900/40', pulse: counts.FORCE_SELL > 0 },
    { label: 'HOLD',       value: counts.HOLD || 0,        color: 'border-gray-700 text-gray-400',   bg: 'bg-gray-900' },
  ]

  return (
    <div className="grid grid-cols-5 gap-3">
      {cards.map(c => (
        <div key={c.label} className={`rounded-xl border ${c.color} ${c.bg} p-4 ${c.pulse ? 'animate-pulse' : ''}`}>
          <div className="text-xs text-gray-500 uppercase tracking-widest mb-1">{c.label}</div>
          <div className={`text-3xl font-black ${c.color.split(' ')[1]}`}>{c.value}</div>
        </div>
      ))}
    </div>
  )
}
