/**
 * ResultsTable — comparison table for backtest results.
 * Works with both single result and multi-result (presets/variants) arrays.
 */

import clsx from 'clsx'

function pct(v)  { return v != null ? `${v.toFixed(2)}%` : '—' }
function num(v, d=2) { return v != null ? Number(v).toFixed(d) : '—' }
function ccy(v)  { return v != null ? `PKR ${Number(v).toLocaleString('en-PK', { maximumFractionDigits: 0 })}` : '—' }

const COLUMNS = [
  { key: 'strategy',         label: 'Strategy' },
  { key: 'return_pct',       label: 'Return %',       fmt: pct,   color: true },
  { key: 'win_rate',         label: 'Win Rate',        fmt: (v) => pct(v) },   // backend already 0-100
  { key: 'profit_factor',    label: 'Profit Factor',   fmt: (v) => num(v), color: true, threshold: 1 },
  { key: 'max_drawdown_pct', label: 'Max DD %',        fmt: (v) => v != null ? `-${Math.abs(v).toFixed(2)}%` : '—', negative: true },
  { key: 'sharpe_ratio',     label: 'Sharpe',          fmt: (v) => num(v) },
  { key: 'trades',           label: 'Trades',          fmt: (v) => v ?? '—' },
  { key: 'winning_trades',   label: 'Wins',            fmt: (v) => v ?? '—' },
  { key: 'losing_trades',    label: 'Losses',          fmt: (v) => v ?? '—' },
  { key: 'final_equity',     label: 'Final Equity',    fmt: ccy },
  { key: 'ticks_used',       label: 'Ticks',           fmt: (v) => v ?? '—' },
]

const SKIP_LABELS = {
  insufficient_data: 'No historical data',
  low_liquidity:     'Low liquidity',
}

function cellColor(col, value) {
  if (!col.color && !col.negative) return 'text-gray-200'
  if (col.negative) return 'text-red-400'
  const threshold = col.threshold ?? 0
  return value > threshold ? 'text-green-400' : value < threshold ? 'text-red-400' : 'text-gray-400'
}

export default function ResultsTable({ results = [] }) {
  if (!results.length) return null

  // Normalize to array
  const rows = Array.isArray(results) ? results : [results]

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-900/80 sticky top-0">
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                className="px-3 py-2.5 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider whitespace-nowrap"
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800/50">
          {rows.map((row, i) => {
            const skipped = row.skipped
            return (
              <tr
                key={i}
                className={clsx(
                  'hover:bg-gray-800/40 transition-colors',
                  skipped && 'opacity-60',
                )}
              >
                {COLUMNS.map((col) => {
                  const raw = row[col.key]
                  const display = col.fmt ? col.fmt(raw) : (raw ?? '—')
                  return (
                    <td
                      key={col.key}
                      className={clsx(
                        'px-3 py-2.5 font-mono tabular-nums text-sm',
                        cellColor(col, raw),
                      )}
                    >
                      {col.key === 'strategy' && skipped ? (
                        <span className="flex items-center gap-2 flex-wrap">
                          <span>{display}</span>
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-yellow-900/40 text-yellow-400 border border-yellow-800 font-sans">
                            {SKIP_LABELS[skipped] ?? skipped}
                          </span>
                        </span>
                      ) : display}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
