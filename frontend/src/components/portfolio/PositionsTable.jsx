/**
 * PositionsTable — open positions with live P&L and sell action.
 */

import clsx from 'clsx'
import { useUIStore }    from '../../store/useUIStore'
import { useMarketStore } from '../../store/useMarketStore'

function PLCell({ value, pct }) {
  if (value == null) return <span className="text-gray-600 font-mono">—</span>
  const pos = value >= 0
  return (
    <div className={clsx('font-mono tabular-nums text-right', pos ? 'text-green-400' : 'text-red-400')}>
      <div>{pos ? '+' : ''}{value.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
      {pct != null && (
        <div className="text-[10px] opacity-70">{pos ? '+' : ''}{pct.toFixed(2)}%</div>
      )}
    </div>
  )
}

function fmt(n, dec = 2) {
  return n != null
    ? n.toLocaleString('en-PK', { minimumFractionDigits: dec, maximumFractionDigits: dec })
    : '—'
}

export default function PositionsTable({ positions = [], onSell }) {
  const setTradeIntent = useUIStore((s) => s.setTradeIntent)
  const signals        = useMarketStore((s) => s.signals)

  // Build symbol → live price map from WebSocket feed
  const livePrice = {}
  signals.forEach((s) => { if (s.current != null) livePrice[s.symbol] = s.current })

  // Enrich each position with live price + recalculated P&L
  const enriched = positions.map((pos) => {
    const live = livePrice[pos.symbol]
    if (live == null) return pos
    const current_value   = live * pos.shares
    const cost_basis      = pos.avg_buy_price * pos.shares
    const unrealized_pl   = current_value - cost_basis
    const unrealized_pl_pct = cost_basis > 0 ? (unrealized_pl / cost_basis) * 100 : null
    return { ...pos, current_price: live, current_value, unrealized_pl, unrealized_pl_pct }
  })

  if (!enriched.length) {
    return (
      <div className="flex items-center justify-center h-28 text-gray-600 text-sm border border-gray-800 rounded-lg">
        No open positions.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-900/80 sticky top-0">
          <tr>
            {['Symbol', 'Shares', 'Avg Price', 'Current', 'Value', 'P&L', 'Actions'].map((h) => (
              <th
                key={h}
                className="px-3 py-2.5 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider whitespace-nowrap"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800/50">
          {enriched.map((pos) => (
            <tr key={pos.symbol} className="hover:bg-gray-800/40 transition-colors">
              <td className="px-3 py-3 font-bold text-white">{pos.symbol}</td>
              <td className="px-3 py-3 font-mono tabular-nums text-gray-200 text-right">
                {fmt(pos.shares, 0)}
              </td>
              <td className="px-3 py-3 font-mono tabular-nums text-gray-400 text-right">
                {fmt(pos.avg_buy_price)}
              </td>
              <td className="px-3 py-3 font-mono tabular-nums text-gray-200 text-right">
                {pos.current_price != null ? fmt(pos.current_price) : <span className="text-gray-600">—</span>}
              </td>
              <td className="px-3 py-3 font-mono tabular-nums text-gray-200 text-right">
                {pos.current_value != null ? fmt(pos.current_value) : <span className="text-gray-600">—</span>}
              </td>
              <td className="px-3 py-3">
                <PLCell value={pos.unrealized_pl} pct={pos.unrealized_pl_pct} />
              </td>
              <td className="px-3 py-3">
                <button
                  onClick={() => {
                    if (onSell) onSell(pos.symbol)
                    setTradeIntent({ symbol: pos.symbol, side: 'sell', price: pos.current_price })
                  }}
                  className="px-2.5 py-1 rounded text-xs font-bold bg-orange-900/40 text-orange-400 border border-orange-800
                             hover:bg-orange-800/60 transition"
                >
                  SELL
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
