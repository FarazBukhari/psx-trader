/**
 * StockTable — sortable, filterable live signals table with Smart Sort support.
 */

import { useEffect, useRef } from 'react'
import SignalBadge from './SignalBadge.jsx'

function PriceCell({ symbol, value, prevPrices }) {
  const prev = prevPrices.current[symbol]
  const flash = value > prev ? 'flash-green' : value < prev ? 'flash-red' : ''
  useEffect(() => { prevPrices.current[symbol] = value })
  return (
    <span className={`font-mono tabular-nums ${flash}`}>
      {value?.toFixed(2) ?? '—'}
    </span>
  )
}

function ScoreBar({ score }) {
  // Max meaningful score ~11500 (FORCE_SELL + all bonuses)
  const pct = Math.min(100, (score / 11500) * 100)
  const color = score >= 10000 ? 'bg-red-500'
    : score >= 1100 ? 'bg-orange-400'
    : score >= 1000 ? 'bg-green-500'
    : 'bg-gray-600'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-gray-500 font-mono text-[10px] w-12 text-right">{Math.round(score)}</span>
    </div>
  )
}

export default function StockTable({ signals, filter, sortKey, sortDir, onSort, smartSort }) {
  const prevPrices = useRef({})

  const filtered = signals.filter(s => {
    if (!filter) return true
    const q = filter.toLowerCase()
    return (
      s.symbol.toLowerCase().includes(q) ||
      (s.sector || '').toLowerCase().includes(q) ||
      (s.signal || '').toLowerCase().includes(q)
    )
  })

  const sorted = [...filtered].sort((a, b) => {
    let av = a[sortKey] ?? (sortDir === 'asc' ? Infinity : -Infinity)
    let bv = b[sortKey] ?? (sortDir === 'asc' ? Infinity : -Infinity)
    if (typeof av === 'string') av = av.toLowerCase()
    if (typeof bv === 'string') bv = bv.toLowerCase()
    if (av < bv) return sortDir === 'asc' ? -1 : 1
    if (av > bv) return sortDir === 'asc' ? 1 : -1
    return 0
  })

  const TH = ({ col, label, cls = '' }) => (
    <th
      className={`px-3 py-2 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider cursor-pointer hover:text-white select-none ${cls}`}
      onClick={() => onSort(col)}
    >
      {label}
      {sortKey === col && (
        <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>
      )}
    </th>
  )

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-900/80 sticky top-0 z-10">
          <tr>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-400 uppercase w-8">#</th>
            <TH col="symbol"       label="Symbol" />
            <TH col="sector"       label="Sector" />
            <TH col="current"      label="Price"       cls="text-right" />
            <TH col="change_pct"   label="Chg %"       cls="text-right" />
            <TH col="volume"       label="Volume"      cls="text-right" />
            <TH col="rsi"          label="RSI"         cls="text-right" />
            <TH col="sma5"         label="SMA5"        cls="text-right" />
            <TH col="signal"       label="Signal"      cls="text-center" />
            <TH col="action_score" label="Score"       cls="text-right" />
            <th className="px-3 py-2 text-xs text-gray-400 font-semibold uppercase">Sources</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800/60">
          {sorted.length === 0 && (
            <tr>
              <td colSpan={11} className="text-center py-12 text-gray-600">No signals match filter.</td>
            </tr>
          )}
          {sorted.map((s, idx) => {
            const chg = s.change_pct ?? 0
            const rsi = s.rsi
            const rsiColor = rsi == null ? 'text-gray-600'
              : rsi <= 30 ? 'text-green-400 font-bold'
              : rsi >= 70 ? 'text-red-400 font-bold'
              : 'text-gray-300'
            const rowHighlight = s.signal === 'FORCE_SELL' ? 'bg-red-900/20 hover:bg-red-900/30'
              : s.signal === 'BUY' ? 'hover:bg-green-900/10'
              : s.signal === 'SELL' ? 'hover:bg-red-900/10'
              : 'hover:bg-gray-800/40'

            return (
              <tr
                key={s.symbol}
                className={`transition-colors ${rowHighlight} ${s.signal_changed ? 'bg-yellow-900/10' : ''}`}
              >
                {/* Rank number */}
                <td className="px-3 py-2.5 text-gray-600 text-xs tabular-nums">{idx + 1}</td>

                <td className="px-3 py-2.5 font-bold text-white">
                  {s.symbol}
                  {s.signal_changed && <span className="ml-1 text-yellow-400 text-[10px]">⚡</span>}
                </td>
                <td className="px-3 py-2.5 text-gray-400 text-xs">{s.sector || '—'}</td>
                <td className="px-3 py-2.5 text-right">
                  <PriceCell symbol={s.symbol} value={s.current} prevPrices={prevPrices} />
                </td>
                <td className={`px-3 py-2.5 text-right font-mono tabular-nums font-semibold ${chg >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {chg >= 0 ? '+' : ''}{chg?.toFixed(2)}%
                </td>
                <td className="px-3 py-2.5 text-right text-gray-300 font-mono tabular-nums text-xs">
                  {s.volume != null
                    ? s.volume >= 1_000_000
                      ? `${(s.volume / 1_000_000).toFixed(1)}M`
                      : `${(s.volume / 1_000).toFixed(0)}K`
                    : '—'}
                </td>
                <td className={`px-3 py-2.5 text-right font-mono tabular-nums text-xs ${rsiColor}`}>
                  {rsi != null ? rsi.toFixed(1) : '—'}
                </td>
                <td className="px-3 py-2.5 text-right text-gray-400 font-mono tabular-nums text-xs">
                  {s.sma5 != null ? s.sma5.toFixed(2) : '—'}
                </td>
                <td className="px-3 py-2.5 text-center">
                  <SignalBadge signal={s.signal} changed={s.signal_changed} />
                </td>
                <td className="px-3 py-2.5">
                  {s.action_score > 0
                    ? <ScoreBar score={s.action_score} />
                    : <span className="text-gray-700 text-xs">—</span>
                  }
                </td>
                <td className="px-3 py-2.5 text-gray-500 text-xs">
                  {(s.signal_sources || []).join(', ') || '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
