/**
 * ResultsTable — comparison table for backtest results.
 * Works with both single result and multi-result (presets/variants) arrays.
 */

import clsx from 'clsx'

function pct(v)    { return v != null ? `${v.toFixed(2)}%` : '—' }
function num(v, d=2) { return v != null ? Number(v).toFixed(d) : '—' }
function ccy(v)    { return v != null ? `PKR ${Number(v).toLocaleString('en-PK', { maximumFractionDigits: 0 })}` : '—' }

// Human-readable descriptions + parameter summaries for each preset
const PRESET_INFO = {
  conservative: {
    label: 'Conservative',
    desc:  'Waits for deep oversold/overbought extremes before acting. Wider SMAs confirm the trend. Fewer trades, lower risk.',
    params: 'RSI 25/75 · SMA 10/30 · SL 4% · Chg 4%',
  },
  default: {
    label: 'Default',
    desc:  'Balanced mid-ground. Standard RSI thresholds and SMA window sizes.',
    params: 'RSI 30/70 · SMA 5/20 · SL 5% · Chg 3%',
  },
  balanced: {
    label: 'Balanced',
    desc:  'Balanced mid-ground. Standard RSI thresholds and SMA window sizes.',
    params: 'RSI 30/70 · SMA 5/20 · SL 5% · Chg 3%',
  },
  aggressive: {
    label: 'Aggressive',
    desc:  'Enters earlier (RSI not yet deeply oversold), uses fast SMAs, tolerates wider drawdowns. More trades, higher volatility.',
    params: 'RSI 35/65 · SMA 3/10 · SL 8% · Chg 2%',
  },
  momentum: {
    label: 'Momentum',
    desc:  'Most trigger-happy RSI bands, reacts to even small price moves. Chases trends aggressively with a medium SMA window.',
    params: 'RSI 40/60 · SMA 5/15 · SL 6% · Chg 1.5%',
  },
}

const COLUMNS = [
  { key: 'strategy',         label: 'Strategy' },
  { key: 'return_pct',       label: 'Return %',       fmt: pct,   color: true },
  { key: 'win_rate',         label: 'Win Rate',        fmt: (v) => pct(v) },
  { key: 'profit_factor',    label: 'Profit Factor',   fmt: (v) => num(v), color: true, threshold: 1 },
  { key: 'max_drawdown_pct', label: 'Max DD %',        fmt: (v) => v != null ? `-${Math.abs(v).toFixed(2)}%` : '—', negative: true },
  { key: 'sharpe_ratio',     label: 'Sharpe',          fmt: (v) => num(v) },
  { key: 'trades',           label: 'Trades',          fmt: (v) => v ?? '—' },
  { key: 'winning_trades',   label: 'Wins',            fmt: (v) => v ?? '—' },
  { key: 'losing_trades',    label: 'Losses',          fmt: (v) => v ?? '—' },
  { key: 'starting_cash',    label: 'Start',           fmt: ccy },
  { key: 'final_equity',     label: 'End',             fmt: ccy },
  { key: '_pl',              label: 'P&L',             fmt: ccy, color: true },
  { key: 'ticks_used',       label: 'Days',            fmt: (v) => v ?? '—' },
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
            const skipped  = row.skipped
            const pl       = (row.final_equity != null && row.starting_cash != null)
              ? row.final_equity - row.starting_cash
              : null
            const info     = PRESET_INFO[row.strategy?.toLowerCase()] ?? null
            const enriched = { ...row, _pl: pl }

            return (
              <tr
                key={i}
                className={clsx(
                  'hover:bg-gray-800/40 transition-colors',
                  skipped && 'opacity-60',
                )}
              >
                {COLUMNS.map((col) => {
                  const raw     = enriched[col.key]
                  const display = col.fmt ? col.fmt(raw) : (raw ?? '—')

                  if (col.key === 'strategy') {
                    return (
                      <td key={col.key} className="px-3 py-2.5 min-w-[220px]">
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-semibold text-gray-100 capitalize font-sans">
                              {info?.label ?? display}
                            </span>
                            {skipped && (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-yellow-900/40 text-yellow-400 border border-yellow-800 font-sans">
                                {SKIP_LABELS[skipped] ?? skipped}
                              </span>
                            )}
                          </div>
                          {info && (
                            <>
                              <span className="text-[11px] text-gray-500 leading-snug font-sans">{info.desc}</span>
                              <span className="text-[10px] text-gray-600 font-mono mt-0.5">{info.params}</span>
                            </>
                          )}
                        </div>
                      </td>
                    )
                  }

                  return (
                    <td
                      key={col.key}
                      className={clsx(
                        'px-3 py-2.5 font-mono tabular-nums text-sm whitespace-nowrap',
                        cellColor(col, raw),
                      )}
                    >
                      {display}
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
