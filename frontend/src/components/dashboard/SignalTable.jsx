/**
 * SignalTable — sortable, filterable live signals table.
 *
 * Columns: Symbol · Price · Change% · Volume · RSI · Signal ·
 *          Confidence · Hold · Risk · Actions
 *
 * Click any row to expand → PredictionPanel.
 */

import { useState, useRef, useEffect, useMemo } from 'react'
import clsx from 'clsx'
import Badge from '../common/Badge'
import { InfoTip } from '../common/Tooltip'
import PredictionPanel from './PredictionPanel'
import { useUIStore }    from '../../store/useUIStore'
import { useMarketStore } from '../../store/useMarketStore'

// ── Constants ─────────────────────────────────────────────────────────────────
const SIGNAL_FILTERS = ['ALL', 'BUY', 'SELL', 'FORCE_SELL', 'HOLD']

const COL_TIPS = {
  symbol:     'Ticker symbol as listed on PSX',
  price:      'Last traded price in PKR',
  change_pct: 'Price change % vs previous close',
  volume:     'Total shares traded today',
  rsi:        'Relative Strength Index (14-period). <30 oversold, >70 overbought.',
  signal:     'Trading signal: BUY / SELL / HOLD / FORCE_SELL',
  confidence: 'Prediction model confidence (0–100%). Higher = stronger conviction.',
  hold_days:  'Suggested holding period in days',
  risk:       'Predicted risk level: LOW / MEDIUM / HIGH',
}

// ── Price flash cell ──────────────────────────────────────────────────────────
function PriceCell({ symbol, value, prevRef }) {
  const prev  = prevRef.current[symbol]
  const flash = value > prev ? 'flash-green' : value < prev ? 'flash-red' : ''
  useEffect(() => { prevRef.current[symbol] = value }, [symbol, value, prevRef])
  return (
    <span className={clsx('font-mono tabular-nums', flash)}>
      {value?.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '—'}
    </span>
  )
}

// ── Action score bar ──────────────────────────────────────────────────────────
function ScoreBar({ score }) {
  const pct   = Math.min(100, (score / 11500) * 100)
  const color = score >= 10000 ? 'bg-red-500'
    : score >= 1100 ? 'bg-orange-400'
    : score >= 1000 ? 'bg-green-500'
    : 'bg-gray-600'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-12 h-1 bg-gray-800 rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-gray-600 font-mono text-[10px]">{Math.round(score)}</span>
    </div>
  )
}

// ── Sortable column header ────────────────────────────────────────────────────
function TH({ colKey, label, tip, sortKey, sortDir, onSort, className }) {
  const active = sortKey === colKey
  return (
    <th
      className={clsx(
        'px-3 py-2.5 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider',
        'cursor-pointer hover:text-white select-none whitespace-nowrap',
        className,
      )}
      onClick={() => onSort(colKey)}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {tip && <InfoTip text={tip} />}
        {active && (
          <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>
        )}
      </span>
    </th>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function SignalTable() {
  const signals        = useMarketStore((s) => s.signals)
  const expandedSym    = useUIStore((s) => s.expandedSymbol)
  const setExpanded    = useUIStore((s) => s.setExpandedSymbol)
  const setTradeIntent = useUIStore((s) => s.setTradeIntent)
  const watchlist      = useUIStore((s) => s.watchlist)
  const toggleWatch    = useUIStore((s) => s.toggleWatch)

  const prevPrices = useRef({})

  const [filter,    setFilter]    = useState('')
  const [sigFilter, setSigFilter] = useState('ALL')
  const [sortKey,   setSortKey]   = useState('action_score')
  const [sortDir,   setSortDir]   = useState('desc')
  const [smartSort, setSmartSort] = useState(true)
  const [minConf,   setMinConf]   = useState(0)   // 0–100 slider

  const handleSort = (col) => {
    setSmartSort(false)
    setSortKey((k) => {
      if (k === col) { setSortDir((d) => d === 'asc' ? 'desc' : 'asc'); return col }
      setSortDir('desc')
      return col
    })
  }

  const filtered = useMemo(() => {
    let list = signals
    if (sigFilter !== 'ALL') list = list.filter((s) => s.signal === sigFilter)
    if (minConf > 0) {
      const thresh = minConf / 100
      list = list.filter((s) => (s.prediction?.confidence ?? 0) >= thresh)
    }
    if (filter.trim()) {
      const q = filter.toLowerCase()
      list = list.filter(
        (s) =>
          s.symbol.toLowerCase().includes(q) ||
          (s.sector || '').toLowerCase().includes(q),
      )
    }
    return list
  }, [signals, sigFilter, minConf, filter])

  const sorted = useMemo(() => {
    // Resolve dot-notation keys like 'prediction.confidence'
    const resolve = (obj, key) => {
      if (!key.includes('.')) return obj[key]
      return key.split('.').reduce((o, k) => o?.[k], obj)
    }
    const missing = sortDir === 'asc' ? Infinity : -Infinity
    const base = [...filtered].sort((a, b) => {
      let av = resolve(a, sortKey) ?? missing
      let bv = resolve(b, sortKey) ?? missing
      if (typeof av === 'string') av = av.toLowerCase()
      if (typeof bv === 'string') bv = bv.toLowerCase()
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    // Float watched symbols to top
    if (watchlist.length === 0) return base
    const watched   = base.filter((s) => watchlist.includes(s.symbol))
    const unwatched = base.filter((s) => !watchlist.includes(s.symbol))
    return [...watched, ...unwatched]
  }, [filtered, sortKey, sortDir, watchlist])

  const thProps = { sortKey, sortDir, onSort: handleSort }

  if (signals.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-600 text-sm">
        Waiting for live signal data…
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* ── Controls ── */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          placeholder="Search symbol or sector…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200
                     placeholder-gray-600 focus:outline-none focus:border-blue-500 w-52"
        />
        <div className="flex gap-1">
          {SIGNAL_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setSigFilter(f)}
              className={clsx(
                'px-2.5 py-1 rounded text-xs font-semibold transition',
                sigFilter === f ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <button
          onClick={() => { setSmartSort(true); setSortKey('action_score'); setSortDir('desc') }}
          className={clsx(
            'flex items-center gap-1 px-2.5 py-1 rounded text-xs font-bold border transition',
            smartSort
              ? 'bg-yellow-500/20 text-yellow-300 border-yellow-600'
              : 'bg-gray-800 text-gray-500 border-gray-700 hover:bg-gray-700',
          )}
        >
          ⚡ SMART SORT
        </button>

        {/* Sort-by quick buttons */}
        <div className="flex gap-1 ml-1">
          {[
            { key: 'prediction.confidence', label: 'Conf' },
            { key: 'volume',                label: 'Vol' },
            { key: 'change_pct',            label: 'Chg%' },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => { setSmartSort(false); setSortKey(key); setSortDir('desc') }}
              className={clsx(
                'px-2 py-1 rounded text-[10px] font-semibold border transition',
                sortKey === key && !smartSort
                  ? 'bg-blue-700 text-white border-blue-600'
                  : 'bg-gray-800 text-gray-500 border-gray-700 hover:border-gray-600',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Min-confidence slider */}
        <div className="flex items-center gap-2 ml-2">
          <span className="text-[10px] text-gray-600 whitespace-nowrap">Min conf</span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minConf}
            onChange={(e) => setMinConf(Number(e.target.value))}
            className="w-20 accent-blue-500 cursor-pointer"
          />
          <span className="text-[10px] text-gray-500 w-8">{minConf}%</span>
          {minConf > 0 && (
            <button onClick={() => setMinConf(0)} className="text-[10px] text-gray-600 hover:text-gray-400">✕</button>
          )}
        </div>

        <span className="text-xs text-gray-600 ml-auto">{sorted.length} stocks</span>
      </div>

      {/* ── Table ── */}
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-900/80 sticky top-0 z-10">
            <tr>
              <th className="px-3 py-2.5 text-[11px] text-gray-600 font-semibold w-6">⭐</th>
              <th className="px-3 py-2.5 text-[11px] text-gray-600 font-semibold w-8">#</th>
              <TH colKey="symbol"     label="Symbol"     tip={COL_TIPS.symbol}     {...thProps} />
              <TH colKey="current"    label="Price"      tip={COL_TIPS.price}      {...thProps} className="text-right" />
              <TH colKey="change_pct" label="Chg %"      tip={COL_TIPS.change_pct} {...thProps} className="text-right" />
              <TH colKey="volume"     label="Vol"        tip={COL_TIPS.volume}     {...thProps} className="text-right" />
              <TH colKey="rsi"        label="RSI"        tip={COL_TIPS.rsi}        {...thProps} className="text-right" />
              <TH colKey="signal"     label="Signal"     tip={COL_TIPS.signal}     {...thProps} className="text-center" />
              <TH colKey="prediction.confidence" label="Conf %" tip={COL_TIPS.confidence} {...thProps} className="text-right" />
              <TH colKey="prediction.hold_days"  label="Hold"   tip={COL_TIPS.hold_days}  {...thProps} className="text-right" />
              <TH colKey="prediction.risk_level" label="Risk"   tip={COL_TIPS.risk}        {...thProps} className="text-center" />
              <TH colKey="action_score" label="Score"    {...thProps} className="text-right" />
              <th className="px-3 py-2.5 text-[11px] text-gray-400 font-semibold uppercase tracking-wider text-center">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {sorted.length === 0 && (
              <tr>
                <td colSpan={13} className="text-center py-10 text-gray-600 text-xs">
                  No signals match filter.
                </td>
              </tr>
            )}
            {sorted.map((s, idx) => {
              const chg      = s.change_pct ?? 0
              const rsi      = s.rsi
              const pred     = s.prediction
              const expanded = expandedSym === s.symbol
              const watched  = watchlist.includes(s.symbol)

              const rsiColor = rsi == null ? 'text-gray-600'
                : rsi <= 30 ? 'text-green-400 font-bold'
                : rsi >= 70 ? 'text-red-400 font-bold'
                : 'text-gray-300'

              const isHighConf   = (pred?.confidence ?? 0) >= 0.7
              const isHighRisk   = pred?.risk_level === 'HIGH'
              const isStrongBuy  = s.signal === 'BUY'  && isHighConf
              const isStrongSell = (s.signal === 'SELL' || s.signal === 'FORCE_SELL') && isHighConf

              const rowBg = s.signal === 'FORCE_SELL' ? 'bg-red-900/15 hover:bg-red-900/25'
                : watched          ? 'bg-yellow-950/30 hover:bg-yellow-900/20'
                : isStrongBuy      ? 'bg-green-900/20 hover:bg-green-900/30'
                : isStrongSell     ? 'bg-orange-900/20 hover:bg-orange-900/30'
                : isHighRisk       ? 'bg-red-900/10 hover:bg-red-900/20'
                : s.signal === 'BUY'  ? 'hover:bg-green-900/10'
                : s.signal === 'SELL' ? 'hover:bg-orange-900/10'
                : 'hover:bg-gray-800/40'

              const conf      = pred?.confidence != null ? `${(pred.confidence * 100).toFixed(0)}%` : '—'
              const holdDays  = pred?.hold_days   != null ? `~${pred.hold_days}d` : '—'
              const risk      = pred?.risk_level  || '—'
              const riskCls   = { LOW: 'text-green-400', MEDIUM: 'text-yellow-400', HIGH: 'text-red-400' }[risk] || 'text-gray-500'

              return (
                <>
                  <tr
                    key={s.symbol}
                    onClick={() => setExpanded(s.symbol)}
                    className={clsx(
                      'transition-colors cursor-pointer',
                      rowBg,
                      expanded && 'bg-gray-800/50',
                      s.signal_changed && 'bg-yellow-900/10',
                    )}
                  >
                    {/* Star / watchlist */}
                    <td className="px-2 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => toggleWatch(s.symbol)}
                        className={clsx(
                          'text-sm leading-none transition',
                          watched ? 'text-yellow-400' : 'text-gray-700 hover:text-yellow-600',
                        )}
                        title={watched ? 'Remove from watchlist' : 'Add to watchlist'}
                      >
                        {watched ? '⭐' : '☆'}
                      </button>
                    </td>
                    <td className="px-3 py-2.5 text-gray-600 text-xs tabular-nums">{idx + 1}</td>

                    <td className="px-3 py-2.5 font-bold text-white whitespace-nowrap">
                      {s.symbol}
                      {s.signal_changed && <span className="ml-1 text-yellow-400 text-[10px]">⚡</span>}
                    </td>

                    <td className="px-3 py-2.5 text-right">
                      <PriceCell symbol={s.symbol} value={s.current} prevRef={prevPrices} />
                    </td>

                    <td className={clsx(
                      'px-3 py-2.5 text-right font-mono tabular-nums text-xs font-semibold',
                      chg >= 0 ? 'text-green-400' : 'text-red-400',
                    )}>
                      {chg >= 0 ? '+' : ''}{chg?.toFixed(2)}%
                    </td>

                    <td className="px-3 py-2.5 text-right text-gray-400 font-mono tabular-nums text-xs">
                      {s.volume != null
                        ? s.volume >= 1_000_000
                          ? `${(s.volume / 1_000_000).toFixed(1)}M`
                          : `${(s.volume / 1_000).toFixed(0)}K`
                        : '—'}
                    </td>

                    <td className={clsx('px-3 py-2.5 text-right font-mono tabular-nums text-xs', rsiColor)}>
                      {rsi != null ? rsi.toFixed(1) : '—'}
                    </td>

                    <td className="px-3 py-2.5 text-center">
                      <Badge variant={s.signal} />
                    </td>

                    <td className={clsx('px-3 py-2.5 text-right font-mono text-xs', isHighConf ? 'text-white font-bold' : 'text-gray-300')}>{conf}</td>
                    <td className="px-3 py-2.5 text-right text-gray-300 font-mono text-xs">{holdDays}</td>
                    <td className={clsx('px-3 py-2.5 text-center text-xs font-semibold', riskCls)}>{risk}</td>

                    <td className="px-3 py-2.5 text-right">
                      {s.action_score > 0
                        ? <ScoreBar score={s.action_score} />
                        : <span className="text-gray-700 text-xs">—</span>}
                    </td>

                    {/* Actions */}
                    <td className="px-3 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-center gap-1">
                        <button
                          onClick={() => setTradeIntent({ symbol: s.symbol, side: 'buy', price: s.current })}
                          className="px-2 py-0.5 rounded text-[10px] font-bold bg-green-900/40 text-green-400 border border-green-800 hover:bg-green-800/60 transition"
                        >
                          BUY
                        </button>
                        <button
                          onClick={() => setTradeIntent({ symbol: s.symbol, side: 'sell', price: s.current })}
                          className="px-2 py-0.5 rounded text-[10px] font-bold bg-orange-900/40 text-orange-400 border border-orange-800 hover:bg-orange-800/60 transition"
                        >
                          SELL
                        </button>
                      </div>
                    </td>
                  </tr>

                  {/* Expanded prediction panel */}
                  {expanded && (
                    <tr key={`${s.symbol}-detail`} className="bg-gray-900/50">
                      <td colSpan={13} className="p-0">
                        <PredictionPanel signal={s} />
                      </td>
                    </tr>
                  )}
                </>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
