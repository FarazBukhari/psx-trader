/**
 * PortfolioBar — top summary strip: total value, cash, unrealized P&L, realized P&L.
 */

import clsx from 'clsx'
import Loader from '../common/Loader'

function PLValue({ value, pct }) {
  if (value == null) return <span className="text-gray-600">—</span>
  const pos = value >= 0
  return (
    <span className={clsx('font-mono font-semibold', pos ? 'text-green-400' : 'text-red-400')}>
      {pos ? '+' : ''}
      {value.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      {pct != null && (
        <span className="text-[11px] ml-1 opacity-70">
          ({pos ? '+' : ''}{pct.toFixed(2)}%)
        </span>
      )}
    </span>
  )
}

function Stat({ label, value, className, children }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider whitespace-nowrap">{label}</span>
      {children || (
        <span className={clsx('text-sm font-semibold font-mono tabular-nums text-gray-100 truncate', className)}>
          {value ?? '—'}
        </span>
      )}
    </div>
  )
}

export default function PortfolioBar({ portfolio, loading }) {
  if (loading && !portfolio) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg px-5 py-3">
        <Loader size="sm" message="Loading portfolio…" />
      </div>
    )
  }
  if (!portfolio) return null

  const p = portfolio
  const fmt = (n) =>
    n != null
      ? n.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : '—'

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg px-5 py-3 flex flex-wrap gap-6 items-center">
      <Stat label="Portfolio Value" value={`PKR ${fmt(p.total_portfolio_value)}`} className="text-white text-base" />
      <div className="w-px h-8 bg-gray-800 hidden md:block" />
      <Stat label="Cash Available" value={`PKR ${fmt(p.cash_available)}`} />
      <div className="w-px h-8 bg-gray-800 hidden md:block" />
      <Stat label="Unrealized P&L">
        <PLValue value={p.unrealized_pl} />
      </Stat>
      <div className="w-px h-8 bg-gray-800 hidden md:block" />
      <Stat label="Realized P&L">
        <PLValue value={p.realized_pl} />
      </Stat>
      <div className="w-px h-8 bg-gray-800 hidden md:block" />
      <Stat label="Total P&L">
        <PLValue value={p.total_pl} pct={p.total_pl_pct} />
      </Stat>
      <div className="w-px h-8 bg-gray-800 hidden md:block" />
      <Stat label="Positions" value={p.position_count ?? 0} />
    </div>
  )
}
