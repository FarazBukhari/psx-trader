/**
 * TradeHistoryTable — closed forward trades with outcome highlighting and sorting.
 *
 * Row colours (Step 7):
 *   STRONG_WIN / WEAK_WIN → green tint
 *   LOSS                  → red tint
 *   BREAKEVEN             → yellow tint
 *
 * Sortable columns: exit_time, symbol, outcome, pnl_pct, duration_minutes.
 */

import { useState, useMemo, useCallback } from 'react'
import clsx from 'clsx'
import Badge from '../common/Badge'
import { usePerformanceStore } from '../../store/usePerformanceStore'

// ── helpers ───────────────────────────────────────────────────────────────────

function pnlPct(trade) {
  const { signal, entry_price: ep, exit_price: xp } = trade
  if (!ep || !xp) return null
  return signal === 'BUY'
    ? (xp - ep) / ep * 100
    : (ep - xp) / ep * 100
}

function fmtPrice(v) {
  if (v == null) return '—'
  return Number(v).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '—'
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtDuration(mins) {
  if (mins == null) return '—'
  const m = Math.round(Number(mins))
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function fmtTime(unix) {
  if (!unix) return '—'
  return new Date(unix * 1000).toLocaleString('en-PK', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── outcome styling ───────────────────────────────────────────────────────────

const OUTCOME_ROW = {
  STRONG_WIN: 'bg-green-950/40 hover:bg-green-950/60',
  WEAK_WIN:   'bg-green-950/20 hover:bg-green-950/40',
  LOSS:       'bg-red-950/40   hover:bg-red-950/60',
  BREAKEVEN:  'bg-yellow-950/30 hover:bg-yellow-950/50',
}

const OUTCOME_BADGE = {
  STRONG_WIN: 'bg-green-500/25 text-green-300 border-green-600',
  WEAK_WIN:   'bg-green-500/15 text-green-400 border-green-800',
  LOSS:       'bg-red-500/25   text-red-300   border-red-600',
  BREAKEVEN:  'bg-yellow-500/20 text-yellow-300 border-yellow-700',
}

function OutcomeBadge({ outcome }) {
  const cls = OUTCOME_BADGE[outcome] || 'bg-gray-700/50 text-gray-400 border-gray-600'
  const label = {
    STRONG_WIN: '★ Strong Win',
    WEAK_WIN:   '↑ Weak Win',
    LOSS:       '↓ Loss',
    BREAKEVEN:  '≈ Breakeven',
  }[outcome] ?? outcome
  return (
    <span className={clsx(
      'inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border whitespace-nowrap',
      cls,
    )}>
      {label}
    </span>
  )
}

// ── sort header ───────────────────────────────────────────────────────────────

function SortTh({ col, label, sortCol, sortDir, onSort, className = '' }) {
  const active = sortCol === col
  return (
    <th
      className={clsx(
        'px-3 py-2 text-[10px] font-semibold uppercase tracking-wider cursor-pointer select-none transition-colors',
        'hover:text-gray-300',
        active ? 'text-blue-400' : 'text-gray-500',
        className,
      )}
      onClick={() => onSort(col)}
    >
      {label}
      {active && <span className="ml-1 opacity-70">{sortDir === 'asc' ? '▲' : '▼'}</span>}
    </th>
  )
}

// ── row ───────────────────────────────────────────────────────────────────────

function HistoryRow({ trade }) {
  const pnl = pnlPct(trade)
  const rowCls = OUTCOME_ROW[trade.outcome] || 'hover:bg-gray-800/30'

  return (
    <tr className={clsx('border-b border-gray-800/40 transition-colors', rowCls)}>
      <td className="px-3 py-2.5 text-white font-bold text-xs">{trade.symbol}</td>
      <td className="px-3 py-2.5">
        <Badge variant={trade.signal} label={trade.signal} />
      </td>
      <td className="px-3 py-2.5">
        <OutcomeBadge outcome={trade.outcome} />
      </td>
      <td className={clsx(
        'px-3 py-2.5 text-right font-mono text-xs font-bold',
        pnl == null ? 'text-gray-600' : pnl >= 0 ? 'text-green-400' : 'text-red-400',
      )}>
        {fmtPct(pnl)}
      </td>
      <td className="px-3 py-2.5 text-right font-mono text-xs text-gray-400">{fmtPrice(trade.entry_price)}</td>
      <td className="px-3 py-2.5 text-right font-mono text-xs text-gray-400">{fmtPrice(trade.exit_price)}</td>
      <td className={clsx(
        'px-3 py-2.5 text-right font-mono text-xs',
        trade.mfe_pct >= 0 ? 'text-green-500' : 'text-red-500',
      )}>
        {fmtPct(trade.mfe_pct)}
      </td>
      <td className={clsx(
        'px-3 py-2.5 text-right font-mono text-xs',
        trade.mae_pct >= 0 ? 'text-green-500' : 'text-red-500',
      )}>
        {fmtPct(trade.mae_pct)}
      </td>
      <td className="px-3 py-2.5 text-right text-xs text-gray-500 font-mono">{fmtDuration(trade.duration_minutes)}</td>
      <td className="px-3 py-2.5 text-right text-xs text-gray-600">{fmtTime(trade.exit_time)}</td>
    </tr>
  )
}

// ── pagination ────────────────────────────────────────────────────────────────

function Pagination({ total, limit, offset, onPage }) {
  const page  = Math.floor(offset / limit)
  const pages = Math.ceil(total / limit)
  if (pages <= 1) return null
  return (
    <div className="flex items-center justify-between px-3 py-2 border-t border-gray-800 text-xs text-gray-500">
      <span>{total} closed trades</span>
      <div className="flex items-center gap-1">
        <button
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
          className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
        >‹ Prev</button>
        <span className="px-2">{page + 1} / {pages}</span>
        <button
          disabled={page >= pages - 1}
          onClick={() => onPage(page + 1)}
          className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed"
        >Next ›</button>
      </div>
    </div>
  )
}

// ── main ──────────────────────────────────────────────────────────────────────

const PAGE_SIZE = 25

export default function TradeHistoryTable() {
  const history      = usePerformanceStore((s) => s.history)
  const historyTotal = usePerformanceStore((s) => s.historyTotal)
  const fetchHistory = usePerformanceStore((s) => s.fetchHistory)
  const loading      = usePerformanceStore((s) => s.loading)

  const [sortCol, setSortCol] = useState('exit_time')
  const [sortDir, setSortDir] = useState('desc')
  const [page, setPage]       = useState(0)

  const handleSort = useCallback((col) => {
    setSortCol((prev) => {
      if (prev === col) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
      else setSortDir('desc')
      return col
    })
  }, [])

  const handlePage = useCallback((p) => {
    setPage(p)
    fetchHistory(PAGE_SIZE, p * PAGE_SIZE)
  }, [fetchHistory])

  // Client-side sort on current page
  const sorted = useMemo(() => {
    const rows = [...history]
    rows.sort((a, b) => {
      let av, bv
      if (sortCol === 'pnl_pct') {
        av = pnlPct(a) ?? -Infinity
        bv = pnlPct(b) ?? -Infinity
      } else {
        av = a[sortCol] ?? ''
        bv = b[sortCol] ?? ''
      }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ?  1 : -1
      return 0
    })
    return rows
  }, [history, sortCol, sortDir])

  if (!loading && history.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 text-xs text-gray-600">
        No closed trades yet — trades close when TP / SL / time-exit triggers.
      </div>
    )
  }

  const thProps = { sortCol, sortDir, onSort: handleSort }

  return (
    <div className="rounded-xl border border-gray-800 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-900 border-b border-gray-800">
              <SortTh col="symbol"           label="Symbol"   {...thProps} className="text-left" />
              <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Signal</th>
              <SortTh col="outcome"          label="Outcome"  {...thProps} className="text-left" />
              <SortTh col="pnl_pct"          label="Return"   {...thProps} className="text-right" />
              <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Entry</th>
              <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Exit</th>
              <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">MFE</th>
              <th className="px-3 py-2 text-right text-[10px] font-semibold text-gray-500 uppercase tracking-wider">MAE</th>
              <SortTh col="duration_minutes" label="Duration" {...thProps} className="text-right" />
              <SortTh col="exit_time"        label="Closed"   {...thProps} className="text-right" />
            </tr>
          </thead>
          <tbody className="bg-gray-950">
            {sorted.map((t) => <HistoryRow key={t.id} trade={t} />)}
          </tbody>
        </table>
      </div>
      <Pagination
        total={historyTotal}
        limit={PAGE_SIZE}
        offset={page * PAGE_SIZE}
        onPage={handlePage}
      />
    </div>
  )
}
