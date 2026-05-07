/**
 * TradeLog — table of every simulated trade from a backtest run.
 * Shows date, type, price, shares, value, fee, and realised P&L.
 */

import { useState } from 'react'
import clsx from 'clsx'

function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString('en-PK', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function fmtCcy(v, forceSign = false) {
  if (v == null) return '—'
  const n = Number(v)
  const sign = forceSign && n > 0 ? '+' : ''
  return `${sign}PKR ${n.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function TradeLog({ tradeLog = [], label = '' }) {
  const [expanded, setExpanded] = useState(false)

  // Only show closed trades (sell entries have realized_pl set)
  // Show all entries but group buys with their subsequent sell
  const trades = tradeLog.filter(Boolean)
  if (!trades.length) return null

  const visible = expanded ? trades : trades.slice(0, 10)
  const hasMore = trades.length > 10

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <span className="text-xs font-bold text-gray-300 uppercase tracking-wider">
            Trade Log{label ? ` — ${label}` : ''}
          </span>
          <span className="text-[10px] text-gray-600">{trades.length} trades</span>
        </div>
        {hasMore && (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[10px] text-gray-600 hover:text-gray-400 transition"
          >
            {expanded ? 'Show less ↑' : `Show all ${trades.length} ↓`}
          </button>
        )}
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-800/60">
            <tr className="text-[10px] text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-2 text-left font-semibold">Date</th>
              <th className="px-4 py-2 text-left font-semibold">Action</th>
              <th className="px-4 py-2 text-right font-semibold">Price</th>
              <th className="px-4 py-2 text-right font-semibold">Shares</th>
              <th className="px-4 py-2 text-right font-semibold">Value</th>
              <th className="px-4 py-2 text-right font-semibold">Fee</th>
              <th className="px-4 py-2 text-right font-semibold">Realised P&L</th>
              <th className="px-4 py-2 text-left font-semibold">Triggers</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/40">
            {visible.map((t, i) => {
              // Derive action from signal field — trade_type can be unreliable
              // when serialised; the signal is always the ground truth.
              const sig         = t.signal?.toUpperCase()
              const isBuy       = sig === 'BUY'
              const isForceSell = sig === 'FORCE_SELL'
              const pl          = t.realized_pl
              const plColor     = pl == null ? 'text-gray-600'
                : pl > 0 ? 'text-green-400' : pl < 0 ? 'text-red-400' : 'text-gray-400'

              const borderCls = isBuy        ? 'border-l-2 border-l-green-700'
                : isForceSell ? 'border-l-2 border-l-red-700'
                :               'border-l-2 border-l-orange-700'

              const badgeCls  = isBuy        ? 'bg-green-900/50 text-green-300 border-green-700'
                : isForceSell ? 'bg-red-900/50 text-red-300 border-red-700'
                :               'bg-orange-900/50 text-orange-300 border-orange-700'

              const badgeLabel = isBuy ? 'BUY' : isForceSell ? 'FORCE SELL' : 'SELL'

              // Sources: the indicators that voted for this signal
              const sources = Array.isArray(t.sources) && t.sources.length
                ? t.sources.join(', ')
                : sig?.toLowerCase() ?? '—'

              return (
                <tr key={i} className={clsx('hover:bg-gray-800/30 transition-colors', borderCls)}>
                  <td className="px-4 py-2 text-gray-400 font-mono whitespace-nowrap">
                    {fmtDate(t.timestamp)}
                  </td>
                  <td className="px-4 py-2">
                    <span className={clsx('px-1.5 py-0.5 rounded text-[10px] font-bold border', badgeCls)}>
                      {badgeLabel}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-gray-200">
                    {Number(t.price).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-gray-300">
                    {t.shares?.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-gray-400">
                    {fmtCcy(t.net_value)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-gray-600">
                    {t.fee != null ? `PKR ${Number(t.fee).toFixed(2)}` : '—'}
                  </td>
                  <td className={clsx('px-4 py-2 text-right font-mono font-semibold', plColor)}>
                    {pl != null ? fmtCcy(pl, true) : '—'}
                  </td>
                  <td className="px-4 py-2 text-gray-500 text-[10px]">
                    {sources}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
