/**
 * SignalTable — signals grouped into separate BUY / SELL / FORCE_SELL tables.
 *
 * Each section is independently collapsible and shows a configurable number
 * of rows by default, with a "Show all" button to expand to the full list.
 *
 * Columns per section:
 *   ⭐ · # · Symbol/Sector · Price · Chg% · Vol · RSI ·
 *   Action · Conf% · Move% · Hold · Risk · Score · Trade buttons
 *
 * Global controls:
 *   Search (symbol / sector) · Action filter (ALL / BUY / SELL / AVOID) ·
 *   Smart sort · Quick-sort buttons · Min-confidence slider
 */

import React, { useState, useRef, useEffect, useMemo } from 'react'
import clsx from 'clsx'
import Badge from '../common/Badge'
import { InfoTip } from '../common/Tooltip'
import PredictionPanel from './PredictionPanel'
import { useUIStore }    from '../../store/useUIStore'
import { useMarketStore } from '../../store/useMarketStore'

// ── Constants ─────────────────────────────────────────────────────────────────
const DEFAULT_ROWS = 10   // rows shown before "Show all" button appears

const ACTION_FILTERS = ['ALL', 'buy', 'sell', 'avoid']

const SIGNAL_GROUPS = [
  { key: 'BUY',        label: 'BUY',        textCls: 'text-green-400',  borderCls: 'border-green-800',  bgCls: 'bg-green-900/20'  },
  { key: 'SELL',       label: 'SELL',       textCls: 'text-orange-400', borderCls: 'border-orange-800', bgCls: 'bg-orange-900/20' },
  { key: 'FORCE_SELL', label: 'FORCE SELL', textCls: 'text-red-400',    borderCls: 'border-red-800',    bgCls: 'bg-red-900/20'    },
]

const COL_TIPS = {
  symbol:      'Ticker symbol and sector as listed on PSX',
  price:       'Last traded price in PKR',
  change_pct:  'Price change % vs previous close (LDCP)',
  volume:      'Total shares traded today',
  rsi:         'Relative Strength Index (14-period). <30 oversold, >70 overbought.',
  pred_action: 'Prediction model trade action: BUY / SELL / AVOID. ⚠ = conflicts with signal engine.',
  confidence:  'Prediction model confidence (0–100%). Higher = stronger conviction.',
  move_pct:    'Expected price move % over the hold period (OLS regression estimate).',
  hold_days:   'Suggested holding period in trading days',
  risk:        'Predicted risk level based on realised volatility: LOW / MEDIUM / HIGH',
}

const ACTION_STYLES = {
  buy:   'bg-green-900/50 text-green-300 border-green-700',
  sell:  'bg-orange-900/50 text-orange-300 border-orange-700',
  avoid: 'bg-gray-800 text-gray-400 border-gray-700',
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

// ── Prediction action badge ───────────────────────────────────────────────────
// Shows "—" for empty/cold-start predictions (confidence=0, no basis).
function ActionBadge({ action, confidence, basis, signalConflict }) {
  const isEmpty = !action || (action === 'avoid' && confidence === 0 && (!basis || basis.length === 0))
  if (isEmpty) return <span className="text-gray-700 text-xs">—</span>

  const cls = ACTION_STYLES[action] || ACTION_STYLES.avoid
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border ${cls}`}>
        {action.toUpperCase()}
      </span>
      {signalConflict && (
        <span title="Prediction action conflicts with signal engine" className="text-yellow-400 text-[11px]">⚠</span>
      )}
    </span>
  )
}

// ── Score bar ─────────────────────────────────────────────────────────────────
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

// ── Sortable header cell ──────────────────────────────────────────────────────
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
        {active && <span className="ml-1 text-blue-400">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </span>
    </th>
  )
}

// ── Signal section (one per BUY / SELL / FORCE_SELL) ─────────────────────────
function SignalSection({
  signalType, label, textCls, borderCls, bgCls,
  signals,
  expandedSym, setExpanded, setTradeIntent, watchlist, toggleWatch,
  sortKey, sortDir, onSort,
  prevPrices,
}) {
  const [collapsed, setCollapsed] = useState(false)
  const [showAll,   setShowAll]   = useState(false)

  const displayed = showAll ? signals : signals.slice(0, DEFAULT_ROWS)
  const hasMore   = signals.length > DEFAULT_ROWS

  const hasConflict = (signal, tradeAction) => {
    if (!tradeAction || tradeAction === 'avoid') return false
    if (signal === 'BUY' && tradeAction === 'sell') return true
    if ((signal === 'SELL' || signal === 'FORCE_SELL') && tradeAction === 'buy') return true
    return false
  }

  const thProps = { sortKey, sortDir, onSort }

  return (
    <div className={clsx('rounded-lg border overflow-hidden', borderCls)}>
      {/* ── Section header / toggle ── */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className={clsx(
          'w-full flex items-center justify-between px-4 py-3 transition-all',
          bgCls, 'hover:brightness-110',
        )}
      >
        <div className="flex items-center gap-2">
          <span className={clsx('text-sm font-bold tracking-wide', textCls)}>{label}</span>
          <span className="text-gray-400 text-xs bg-gray-900/60 px-2 py-0.5 rounded-full">
            {signals.length} stock{signals.length !== 1 ? 's' : ''}
          </span>
        </div>
        <span className={clsx('text-gray-500 text-xs transition-transform duration-200', collapsed && 'rotate-180')}>
          ▼
        </span>
      </button>

      {!collapsed && (
        <>
          {/* ── Table ── */}
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-900/80 sticky top-0" style={{ zIndex: 20 }}>
                <tr>
                  <th className="px-3 py-2.5 text-[11px] text-gray-600 font-semibold w-6">⭐</th>
                  <th className="px-3 py-2.5 text-[11px] text-gray-600 font-semibold w-8">#</th>
                  <TH colKey="symbol"                       label="Symbol"  tip={COL_TIPS.symbol}      {...thProps} />
                  <TH colKey="current"                      label="Price"   tip={COL_TIPS.price}       {...thProps} className="text-right" />
                  <TH colKey="change_pct"                   label="Chg %"   tip={COL_TIPS.change_pct}  {...thProps} className="text-right" />
                  <TH colKey="volume"                       label="Vol"     tip={COL_TIPS.volume}      {...thProps} className="text-right" />
                  <TH colKey="rsi"                          label="RSI"     tip={COL_TIPS.rsi}         {...thProps} className="text-right" />
                  <TH colKey="prediction.trade_action"      label="Action"  tip={COL_TIPS.pred_action} {...thProps} className="text-center" />
                  <TH colKey="prediction.confidence"        label="Conf %"  tip={COL_TIPS.confidence}  {...thProps} className="text-right" />
                  <TH colKey="prediction.expected_move_pct" label="Move %"  tip={COL_TIPS.move_pct}    {...thProps} className="text-right" />
                  <TH colKey="prediction.hold_days"         label="Hold"    tip={COL_TIPS.hold_days}   {...thProps} className="text-right" />
                  <TH colKey="prediction.risk"              label="Risk"    tip={COL_TIPS.risk}        {...thProps} className="text-center" />
                  <TH colKey="action_score"                 label="Score"                              {...thProps} className="text-right" />
                  <th className="px-3 py-2.5 text-[11px] text-gray-400 font-semibold uppercase tracking-wider text-center">
                    Trade
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {displayed.map((s, idx) => {
                  const chg        = s.change_pct ?? 0
                  const rsi        = s.rsi
                  const pred       = s.prediction
                  const expanded   = expandedSym === s.symbol
                  const watched    = watchlist.includes(s.symbol)
                  const tradeAction = pred?.trade_action
                  const confidence  = pred?.confidence ?? 0
                  const basis       = pred?.basis ?? []
                  const conflict    = hasConflict(s.signal, tradeAction)

                  const rsiColor = rsi == null ? 'text-gray-600'
                    : rsi <= 30 ? 'text-green-400 font-bold'
                    : rsi >= 70 ? 'text-red-400 font-bold'
                    : 'text-gray-300'

                  const isHighConf = confidence >= 0.7
                  const riskRaw    = pred?.risk
                  const isHighRisk = riskRaw === 'high'

                  const rowBg = s.stale
                    ? 'opacity-50 hover:opacity-70'
                    : conflict             ? 'bg-yellow-900/10 hover:bg-yellow-900/20'
                    : signalType === 'FORCE_SELL' ? 'bg-red-900/15 hover:bg-red-900/25'
                    : watched              ? 'bg-yellow-950/30 hover:bg-yellow-900/20'
                    : isHighConf && signalType === 'BUY'  ? 'bg-green-900/20 hover:bg-green-900/30'
                    : isHighConf && signalType === 'SELL' ? 'bg-orange-900/20 hover:bg-orange-900/30'
                    : isHighRisk           ? 'bg-red-900/10 hover:bg-red-900/20'
                    : signalType === 'BUY'  ? 'hover:bg-green-900/10'
                    : signalType === 'SELL' ? 'hover:bg-orange-900/10'
                    : 'hover:bg-gray-800/40'

                  const confLabel  = confidence > 0 ? `${(confidence * 100).toFixed(0)}%` : '—'
                  const holdLabel  = pred?.hold_days != null ? `~${pred.hold_days}d` : '—'
                  const movePct    = pred?.expected_move_pct != null ? pred.expected_move_pct : null
                  const risk       = riskRaw ? riskRaw.toUpperCase() : '—'
                  const riskCls    = { LOW: 'text-green-400', MEDIUM: 'text-yellow-400', HIGH: 'text-red-400' }[risk] || 'text-gray-500'

                  return (
                    <React.Fragment key={s.symbol}>
                      <tr
                        onClick={() => setExpanded(expanded ? null : s.symbol)}
                        className={clsx(
                          'transition-colors cursor-pointer',
                          rowBg,
                          expanded && 'bg-gray-800/50',
                          s.signal_changed && !s.stale && 'bg-yellow-900/10',
                        )}
                      >
                        {/* Watchlist star */}
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

                        {/* Symbol + sector */}
                        <td className="px-3 py-2.5 whitespace-nowrap">
                          <div className="flex items-center gap-1">
                            <span className="font-bold text-white">{s.symbol}</span>
                            {s.signal_changed && !s.stale && (
                              <span className="text-yellow-400 text-[10px]">⚡</span>
                            )}
                          </div>
                          {s.sector && (
                            <div className="text-[10px] text-gray-600 mt-0.5 truncate max-w-[110px]" title={s.sector}>
                              {s.sector}
                            </div>
                          )}
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
                          <ActionBadge
                            action={tradeAction}
                            confidence={confidence}
                            basis={basis}
                            signalConflict={conflict}
                          />
                        </td>

                        <td className={clsx('px-3 py-2.5 text-right font-mono text-xs', isHighConf ? 'text-white font-bold' : 'text-gray-300')}>
                          {confLabel}
                        </td>

                        <td className={clsx(
                          'px-3 py-2.5 text-right font-mono tabular-nums text-xs font-semibold',
                          movePct == null ? 'text-gray-700'
                            : movePct > 0 ? 'text-green-400'
                            : movePct < 0 ? 'text-red-400'
                            : 'text-gray-500',
                        )}>
                          {movePct != null ? `${movePct > 0 ? '+' : ''}${movePct.toFixed(1)}%` : '—'}
                        </td>

                        <td className="px-3 py-2.5 text-right text-gray-300 font-mono text-xs">{holdLabel}</td>

                        <td className={clsx('px-3 py-2.5 text-center text-xs font-semibold', riskCls)}>{risk}</td>

                        <td className="px-3 py-2.5 text-right">
                          {s.action_score > 0
                            ? <ScoreBar score={s.action_score} />
                            : <span className="text-gray-700 text-xs">—</span>}
                        </td>

                        {/* Trade action buttons */}
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
                        <tr className="bg-gray-900/50">
                          <td colSpan={14} className="p-0">
                            <PredictionPanel signal={s} />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* ── Show all / Show less ── */}
          {hasMore && (
            <div className="flex justify-center py-2.5 border-t border-gray-800/60">
              <button
                onClick={() => setShowAll((v) => !v)}
                className="text-xs text-blue-400 hover:text-blue-300 transition px-4 py-1.5 rounded bg-blue-900/20 border border-blue-800/50"
              >
                {showAll
                  ? '▲ Show less'
                  : `▼ Show all ${signals.length} stocks`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function SignalTable() {
  const signals        = useMarketStore((s) => s.signals)
  const isStale        = useMarketStore((s) => s.isStale)
  const expandedSym    = useUIStore((s) => s.expandedSymbol)
  const setExpanded    = useUIStore((s) => s.setExpandedSymbol)
  const setTradeIntent = useUIStore((s) => s.setTradeIntent)
  const watchlist      = useUIStore((s) => s.watchlist)
  const toggleWatch    = useUIStore((s) => s.toggleWatch)

  const prevPrices = useRef({})

  const [textFilter,   setTextFilter]   = useState('')
  const [actionFilter, setActionFilter] = useState('ALL')
  const [sortKey,      setSortKey]      = useState('_smart')
  const [sortDir,      setSortDir]      = useState('desc')
  const [smartSort,    setSmartSort]    = useState(true)
  const [minConf,      setMinConf]      = useState(0)

  const handleSort = (col) => {
    setSmartSort(false)
    setSortKey((k) => {
      if (k === col) { setSortDir((d) => d === 'asc' ? 'desc' : 'asc'); return col }
      setSortDir('desc')
      return col
    })
  }

  // ── Composite smart-sort score ────────────────────────────────────────────
  // Ranks stocks within each signal section by prediction quality.
  // Factors (all normalised to ~0-2 range before weighting):
  //   • confidence        — primary conviction signal  (0–1 from prediction engine)
  //   • |expected_move|   — how large the predicted move is  (% / 10 to normalise)
  //   • actionBoost       — buy/sell predictions ranked above avoid
  //   • riskPenalty       — high-risk stocks ranked down
  //   • action_score      — signal-engine strength as a secondary tiebreaker
  const smartScore = (s) => {
    const pred      = s.prediction
    const conf      = pred?.confidence        ?? 0
    const move      = Math.abs(pred?.expected_move_pct ?? 0)
    const action    = pred?.trade_action      ?? 'avoid'
    const actionBoost  = (action === 'buy' || action === 'sell') ? 1.5 : 1.0
    const riskPenalty  = { low: 1.0, medium: 0.85, high: 0.70 }[pred?.risk ?? 'medium'] ?? 0.85
    const predScore    = conf * (1 + move / 10) * actionBoost * riskPenalty
    const sigScore     = (s.action_score ?? 0) / 11500   // normalise to 0-1
    return predScore * 0.70 + sigScore * 0.30
  }

  // ── Filter + sort the full signal list, then split into groups ────────────
  const processed = useMemo(() => {
    let list = signals

    // Text search
    if (textFilter.trim()) {
      const q = textFilter.toLowerCase()
      list = list.filter(
        (s) => s.symbol.toLowerCase().includes(q) || (s.sector || '').toLowerCase().includes(q),
      )
    }

    // Action filter (prediction.trade_action)
    if (actionFilter !== 'ALL') {
      list = list.filter((s) => (s.prediction?.trade_action ?? 'avoid') === actionFilter)
    }

    // Min-confidence filter
    if (minConf > 0) {
      const thresh = minConf / 100
      list = list.filter((s) => (s.prediction?.confidence ?? 0) >= thresh)
    }

    // Sort
    if (smartSort) {
      list = [...list].sort((a, b) => smartScore(b) - smartScore(a))
    } else {
      const resolve = (obj, key) =>
        key.includes('.') ? key.split('.').reduce((o, k) => o?.[k], obj) : obj[key]
      const missing = sortDir === 'asc' ? Infinity : -Infinity
      list = [...list].sort((a, b) => {
        let av = resolve(a, sortKey) ?? missing
        let bv = resolve(b, sortKey) ?? missing
        if (typeof av === 'string') av = av.toLowerCase()
        if (typeof bv === 'string') bv = bv.toLowerCase()
        if (av < bv) return sortDir === 'asc' ? -1 : 1
        if (av > bv) return sortDir === 'asc' ? 1 : -1
        return 0
      })
    }

    // Float watched symbols to top
    if (watchlist.length > 0) {
      const watched   = list.filter((s) =>  watchlist.includes(s.symbol))
      const unwatched = list.filter((s) => !watchlist.includes(s.symbol))
      list = [...watched, ...unwatched]
    }

    return list
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signals, textFilter, actionFilter, minConf, sortKey, sortDir, smartSort, watchlist])

  // Split by signal type
  const bySignal = useMemo(() => ({
    BUY:        processed.filter((s) => s.signal === 'BUY'),
    SELL:       processed.filter((s) => s.signal === 'SELL'),
    FORCE_SELL: processed.filter((s) => s.signal === 'FORCE_SELL'),
  }), [processed])

  const noResults = bySignal.BUY.length === 0 && bySignal.SELL.length === 0 && bySignal.FORCE_SELL.length === 0

  if (signals.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-600 text-sm">
        Waiting for live signal data…
      </div>
    )
  }

  const sectionProps = {
    expandedSym, setExpanded, setTradeIntent, watchlist, toggleWatch,
    prevPrices, sortKey, sortDir, onSort: handleSort,
  }

  return (
    <div className={clsx('space-y-3 transition-opacity duration-300', isStale && 'opacity-60')}>
      {/* ── Controls ── */}
      <div className="flex flex-wrap items-center gap-2">
        {isStale && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold
                           bg-red-950 border border-red-800 text-red-400 shrink-0">
            ⚠ STALE DATA
            <InfoTip text="Market data is stale — signals may be outdated. Live prices have not been received from PSX recently." />
          </span>
        )}
        <input
          type="text"
          placeholder="Search symbol or sector…"
          value={textFilter}
          onChange={(e) => setTextFilter(e.target.value)}
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200
                     placeholder-gray-600 focus:outline-none focus:border-blue-500 w-52"
        />

        {/* Action filter */}
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-gray-600 mr-0.5">Action:</span>
          {ACTION_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setActionFilter(f)}
              className={clsx(
                'px-2.5 py-1 rounded text-xs font-semibold transition',
                actionFilter === f
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
              )}
            >
              {f === 'ALL' ? 'ALL' : f.toUpperCase()}
            </button>
          ))}
        </div>

        <button
          onClick={() => { setSmartSort(true); setSortKey('_smart') }}
          title="Ranks by: confidence × |expected move| × actionability × risk — within each signal group"
          className={clsx(
            'flex items-center gap-1 px-2.5 py-1 rounded text-xs font-bold border transition',
            smartSort
              ? 'bg-yellow-500/20 text-yellow-300 border-yellow-600'
              : 'bg-gray-800 text-gray-500 border-gray-700 hover:bg-gray-700',
          )}
        >
          ⚡ SMART SORT
        </button>

        {/* Quick-sort buttons */}
        <div className="flex gap-1 ml-1">
          {[
            { key: 'prediction.confidence',       label: 'Conf'  },
            { key: 'volume',                       label: 'Vol'   },
            { key: 'change_pct',                   label: 'Chg%'  },
            { key: 'prediction.expected_move_pct', label: 'Move%' },
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
            type="range" min={0} max={100} step={5}
            value={minConf}
            onChange={(e) => setMinConf(Number(e.target.value))}
            className="w-20 accent-blue-500 cursor-pointer"
          />
          <span className="text-[10px] text-gray-500 w-8">{minConf}%</span>
          {minConf > 0 && (
            <button onClick={() => setMinConf(0)} className="text-[10px] text-gray-600 hover:text-gray-400">✕</button>
          )}
        </div>

        <span className="text-xs text-gray-600 ml-auto">{processed.length} stocks</span>
      </div>

      {/* ── Signal sections ── */}
      {SIGNAL_GROUPS.map((group) =>
        bySignal[group.key].length > 0 ? (
          <SignalSection
            key={group.key}
            signalType={group.key}
            label={group.label}
            textCls={group.textCls}
            borderCls={group.borderCls}
            bgCls={group.bgCls}
            signals={bySignal[group.key]}
            {...sectionProps}
          />
        ) : null,
      )}

      {noResults && (
        <div className="text-center py-10 text-gray-600 text-xs">
          No signals match your filters.
        </div>
      )}
    </div>
  )
}
