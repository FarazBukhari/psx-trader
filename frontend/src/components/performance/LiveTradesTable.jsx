/**
 * LiveTradesTable — currently OPEN forward trades.
 *
 * Columns: symbol, signal, entry price, current MFE, current MAE,
 *          duration (live, computed from entry_time), status dot.
 */

import { useMemo } from 'react'
import clsx from 'clsx'
import Badge from '../common/Badge'
import { usePerformanceStore } from '../../store/usePerformanceStore'

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtPrice(v) {
  if (v == null) return '—'
  return Number(v).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(v, showSign = true) {
  if (v == null || isNaN(v)) return '—'
  const n = Number(v)
  const sign = showSign && n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

/** Live elapsed duration from entry_time (Unix s) to now. */
function useLiveDuration(entryTimeUnix) {
  const mins = Math.floor((Date.now() / 1000 - entryTimeUnix) / 60)
  if (mins < 60) return `${mins}m`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return `${h}h ${m}m`
}

function fmtTime(unix) {
  if (!unix) return '—'
  return new Date(unix * 1000).toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' })
}

// ── row ───────────────────────────────────────────────────────────────────────

function LiveRow({ trade }) {
  const duration = useLiveDuration(trade.entry_time)

  // Approximate live MFE/MAE from stored extremes
  const mfe = trade.mfe_pct
  const mae = trade.mae_pct

  return (
    <tr className="border-b border-gray-800/60 hover:bg-gray-800/30 transition-colors">
      {/* Symbol */}
      <td className="px-3 py-2.5 text-white font-bold text-xs">{trade.symbol}</td>

      {/* Signal badge */}
      <td className="px-3 py-2.5">
        <Badge variant={trade.signal} label={trade.signal} />
      </td>

      {/* Entry price */}
      <td className="px-3 py-2.5 text-right font-mono text-xs text-gray-300">
        {fmtPrice(trade.entry_price)}
      </td>

      {/* MFE */}
      <td className={clsx(
        'px-3 py-2.5 text-right font-mono text-xs font-semibold',
        mfe >= 0 ? 'text-green-400' : 'text-red-400',
      )}>
        {fmtPct(mfe)}
      </td>

      {/* MAE */}
      <td className={clsx(
        'px-3 py-2.5 text-right font-mono text-xs font-semibold',
        mae >= 0 ? 'text-green-400' : 'text-red-400',
      )}>
        {fmtPct(mae)}
      </td>

      {/* Duration */}
      <td className="px-3 py-2.5 text-right text-xs text-gray-500 font-mono">
        {duration}
      </td>

      {/* Entry time */}
      <td className="px-3 py-2.5 text-right text-xs text-gray-600">
        {fmtTime(trade.entry_time)}
      </td>

      {/* Live dot */}
      <td className="px-3 py-2.5 text-center">
        <span className="inline-block w-2 h-2 rounded-full bg-yellow-400 animate-pulse" title="Tracking" />
      </td>
    </tr>
  )
}

// ── main ──────────────────────────────────────────────────────────────────────

export default function LiveTradesTable() {
  const liveTrades = usePerformanceStore((s) => s.liveTrades)
  const loading    = usePerformanceStore((s) => s.loading)

  if (!loading && liveTrades.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 text-xs text-gray-600">
        No open trades — waiting for next actionable signal.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-gray-900 border-b border-gray-800">
            <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Symbol</th>
            <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Signal</th>
            <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Entry</th>
            <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">MFE</th>
            <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">MAE</th>
            <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Age</th>
            <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Opened</th>
            <th className="px-3 py-2 text-center text-[10px] font-semibold text-gray-500 uppercase tracking-wider">●</th>
          </tr>
        </thead>
        <tbody className="bg-gray-950">
          {liveTrades.map((t) => (
            <LiveRow key={t.id} trade={t} />
          ))}
        </tbody>
      </table>
    </div>
  )
}
