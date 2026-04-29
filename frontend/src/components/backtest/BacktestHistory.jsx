/**
 * BacktestHistory — table of past backtest runs (session-only).
 * Reads from useBacktestStore. Sortable by return %.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { useBacktestStore } from '../../store/useBacktestStore'

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function pct(v, decimals = 1) {
  if (v == null) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

export default function BacktestHistory() {
  const history      = useBacktestStore((s) => s.history)
  const clearHistory = useBacktestStore((s) => s.clearHistory)
  const [sortDir, setSortDir] = useState('desc')  // sort by return_pct

  if (history.length === 0) return null

  const sorted = [...history].sort((a, b) => {
    const av = a.return_pct ?? -Infinity
    const bv = b.return_pct ?? -Infinity
    return sortDir === 'desc' ? bv - av : av - bv
  })

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">
          Run History ({history.length})
        </h3>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setSortDir((d) => d === 'desc' ? 'asc' : 'desc')}
            className="text-[10px] text-gray-600 hover:text-gray-400 transition flex items-center gap-0.5"
          >
            Return% {sortDir === 'desc' ? '↓' : '↑'}
          </button>
          <button
            onClick={clearHistory}
            className="text-[10px] text-gray-700 hover:text-red-500 transition"
          >
            Clear
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="text-gray-600 uppercase tracking-wider">
              <th className="pb-2 text-left pr-4 font-semibold">Time</th>
              <th className="pb-2 text-left pr-4 font-semibold">Symbol</th>
              <th className="pb-2 text-left pr-4 font-semibold">Mode</th>
              <th className="pb-2 text-left pr-4 font-semibold">Best Strategy</th>
              <th className="pb-2 text-right pr-4 font-semibold">Return %</th>
              <th className="pb-2 text-right pr-4 font-semibold">Trades</th>
              <th className="pb-2 text-right font-semibold">Win Rate</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {sorted.map((run) => {
              const ret = run.return_pct
              const retColor = ret == null ? 'text-gray-600'
                : ret >= 0 ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'
              return (
                <tr key={run.id} className="hover:bg-gray-800/30 transition-colors">
                  <td className="py-1.5 pr-4 text-gray-600 font-mono">{fmtTime(run.ts)}</td>
                  <td className="py-1.5 pr-4 text-white font-bold">{run.symbol}</td>
                  <td className="py-1.5 pr-4 text-gray-400">{run.mode}</td>
                  <td className="py-1.5 pr-4 text-gray-300 max-w-[140px] truncate">{run.strategy}</td>
                  <td className={clsx('py-1.5 pr-4 text-right font-mono', retColor)}>{pct(ret)}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-400 font-mono">
                    {run.total_trades ?? '—'}
                  </td>
                  <td className="py-1.5 text-right text-gray-400 font-mono">
                    {run.win_rate != null ? pct(run.win_rate * 100, 0) : '—'}
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
