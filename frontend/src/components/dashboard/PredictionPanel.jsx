/**
 * PredictionPanel — expanded row detail showing all prediction + signal data.
 * Renders inside the SignalTable when a row is clicked.
 *
 * Shows every field the backend sends:
 *   Prediction: direction, confidence, trade_action, time_horizon, hold_days,
 *               risk, expected_move_pct, reward_risk_ratio, basis
 *   Signal:     signal, prev_signal, signal_sources, action_score, horizon,
 *               rsi, sma5, sma20
 *   OHLC:       current, ldcp, open, high, low
 */

import clsx from 'clsx'
import Badge from '../common/Badge'

// ── Colour maps ───────────────────────────────────────────────────────────────
const RISK_COLOR = {
  LOW:    'text-green-400',
  MEDIUM: 'text-yellow-400',
  HIGH:   'text-red-400',
}

const DIR_COLOR = {
  up:      'text-green-400',
  down:    'text-red-400',
  neutral: 'text-gray-500',
}

const DIR_ICON = {
  up:      '↑',
  down:    '↓',
  neutral: '→',
}

const ACTION_STYLES = {
  buy:   'bg-green-900/60 text-green-300 border-green-700',
  sell:  'bg-orange-900/60 text-orange-300 border-orange-700',
  avoid: 'bg-gray-800 text-gray-500 border-gray-600',
}

// ── Small stat cell ───────────────────────────────────────────────────────────
function Stat({ label, value, className = 'text-gray-200' }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-[60px]">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider whitespace-nowrap">{label}</span>
      <span className={clsx('text-sm font-semibold', className)}>{value ?? '—'}</span>
    </div>
  )
}

// ── Section label ─────────────────────────────────────────────────────────────
function SectionLabel({ children }) {
  return (
    <div className="text-[10px] text-gray-600 uppercase tracking-widest font-semibold mb-2 border-b border-gray-800 pb-1">
      {children}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function PredictionPanel({ signal }) {
  const pred = signal?.prediction

  // ── Derive display values ─────────────────────────────────────────────────
  // Prediction fields
  const dir         = pred?.direction    || 'neutral'
  const conf        = pred?.confidence   != null ? `${(pred.confidence * 100).toFixed(0)}%` : null
  const tradeAction = pred?.trade_action                              // "buy" | "sell" | "avoid"
  const timeHorizon = pred?.time_horizon                              // "~3 days" | "n/a"
  const holdDays    = pred?.hold_days    != null ? `~${pred.hold_days}d` : null
  const riskRaw     = pred?.risk                                      // "low" | "medium" | "high"
  const riskDisplay = riskRaw ? riskRaw.toUpperCase() : null
  const riskCls     = RISK_COLOR[riskDisplay] || 'text-gray-400'
  const movePct     = pred?.expected_move_pct != null ? pred.expected_move_pct : null
  const rewardRisk  = pred?.reward_risk_ratio ?? pred?.reward_risk    // fallback for old cache

  // Signal fields
  const prevSignal  = signal?.prev_signal !== '—' ? signal?.prev_signal : null
  const rsi         = signal?.rsi
  const sma5        = signal?.sma5
  const sma20       = signal?.sma20
  const actionScore = signal?.action_score
  const horizon     = signal?.horizon

  // OHLC
  const current = signal?.current
  const ldcp    = signal?.ldcp
  const open    = signal?.open
  const high    = signal?.high
  const low     = signal?.low

  // Conflict: signal engine and prediction model disagree
  const signalDir  = signal?.signal
  const conflict   = tradeAction && tradeAction !== 'avoid' && (
    (signalDir === 'BUY'  && tradeAction === 'sell') ||
    ((signalDir === 'SELL' || signalDir === 'FORCE_SELL') && tradeAction === 'buy')
  )

  const fmt = (v) => v != null ? v.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'

  return (
    <div className="px-5 py-4 bg-gray-950/80 border-t border-gray-800/60 space-y-5">

      {/* ── Conflict banner ──────────────────────────────────────────────── */}
      {conflict && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-yellow-900/30 border border-yellow-700/50 text-yellow-300 text-xs">
          <span className="text-base">⚠</span>
          <span>
            <strong>Signal conflict:</strong> The signal engine says <strong>{signalDir}</strong> but the
            prediction model says <strong>{tradeAction?.toUpperCase()}</strong>. Consider waiting for
            alignment before acting.
          </span>
        </div>
      )}

      {/* ── No prediction yet ────────────────────────────────────────────── */}
      {!pred && (
        <p className="text-xs text-gray-600 italic">
          No prediction available yet — price history still accumulating (need ≥10 ticks).
        </p>
      )}

      {/* ── Prediction section ───────────────────────────────────────────── */}
      {pred && (
        <div>
          <SectionLabel>Prediction Model</SectionLabel>
          <div className="flex flex-wrap gap-6 items-start">

            {/* Direction */}
            <div className="flex items-center gap-2">
              <span className={clsx('text-3xl font-black leading-none', DIR_COLOR[dir])}>
                {DIR_ICON[dir]}
              </span>
              <div>
                <div className={clsx('text-base font-bold uppercase', DIR_COLOR[dir])}>{dir}</div>
                <div className="text-[10px] text-gray-600">Direction</div>
              </div>
            </div>

            {/* Trade Action (the model's actual recommendation) */}
            <div>
              <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Trade Action</div>
              {tradeAction ? (
                <span className={`inline-flex items-center px-3 py-1 rounded text-xs font-bold border ${ACTION_STYLES[tradeAction] || ACTION_STYLES.avoid}`}>
                  {tradeAction === 'avoid' ? '⊘ AVOID' : tradeAction === 'buy' ? '▲ BUY' : '▼ SELL'}
                </span>
              ) : (
                <span className="text-gray-600 text-xs">—</span>
              )}
            </div>

            <Stat label="Confidence"    value={conf}          className={conf && parseInt(conf) >= 70 ? 'text-white font-bold' : 'text-gray-200'} />
            <Stat label="Expected Move" value={movePct != null ? `${movePct > 0 ? '+' : ''}${movePct.toFixed(1)}%` : null}
              className={movePct == null ? 'text-gray-600' : movePct > 0 ? 'text-green-400' : movePct < 0 ? 'text-red-400' : 'text-gray-400'} />
            <Stat label="Hold Period"   value={timeHorizon || holdDays}   className="text-gray-200" />
            <Stat label="Risk"          value={riskDisplay}  className={riskCls} />
            <Stat
              label="Reward / Risk"
              value={rewardRisk != null ? rewardRisk.toFixed(2) : null}
              className={
                rewardRisk == null  ? 'text-gray-600'
                : rewardRisk >= 2   ? 'text-green-400'
                : rewardRisk >= 1   ? 'text-yellow-400'
                : 'text-red-400'
              }
            />
          </div>

          {/* Basis */}
          {pred.basis?.length > 0 && (
            <div className="mt-3">
              <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1.5">Prediction Basis</div>
              <div className="flex flex-wrap gap-1.5">
                {pred.basis.map((b, i) => (
                  <span key={i} className="px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-xs text-gray-300">
                    {b}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Signal section ───────────────────────────────────────────────── */}
      <div>
        <SectionLabel>Signal Engine</SectionLabel>
        <div className="flex flex-wrap gap-6 items-start">

          {/* Current signal */}
          <div>
            <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Signal</div>
            <Badge variant={signalDir} />
            {prevSignal && (
              <div className="text-[10px] text-gray-600 mt-1">was: <span className="text-gray-400">{prevSignal}</span></div>
            )}
          </div>

          <Stat label="Action Score" value={actionScore != null ? Math.round(actionScore) : null} className="font-mono text-gray-300" />
          <Stat label="Horizon"      value={horizon?.toUpperCase()} className="text-blue-400" />
          <Stat label="RSI (14)"
            value={rsi != null ? rsi.toFixed(1) : null}
            className={rsi == null ? 'text-gray-600' : rsi <= 30 ? 'text-green-400 font-bold' : rsi >= 70 ? 'text-red-400 font-bold' : 'text-gray-300'}
          />
          <Stat label="SMA 5"  value={sma5  != null ? sma5.toFixed(2)  : null} className="font-mono text-gray-300" />
          <Stat label="SMA 20" value={sma20 != null ? sma20.toFixed(2) : null} className="font-mono text-gray-300" />

        </div>

        {/* Signal sources */}
        {signal?.signal_sources?.length > 0 && (
          <div className="mt-3">
            <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1.5">Triggered By</div>
            <div className="flex flex-wrap gap-1.5">
              {signal.signal_sources.map((src, i) => (
                <span key={i} className="px-2 py-0.5 rounded bg-blue-900/30 border border-blue-800/50 text-xs text-blue-300">
                  {src}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── OHLC section ─────────────────────────────────────────────────── */}
      {(current || ldcp || open || high || low) && (
        <div>
          <SectionLabel>Price Data</SectionLabel>
          <div className="flex flex-wrap gap-6 items-start">
            <Stat label="Current"    value={fmt(current)} className="font-mono text-white font-bold" />
            <Stat label="LDCP"       value={fmt(ldcp)}    className="font-mono text-gray-300" />
            <Stat label="Open"       value={fmt(open)}    className="font-mono text-gray-300" />
            <Stat label="High"       value={fmt(high)}    className="font-mono text-green-400" />
            <Stat label="Low"        value={fmt(low)}     className="font-mono text-red-400" />
            {high != null && low != null && high > 0 && (
              <div>
                <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Day Range</div>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[10px] text-red-400 font-mono">{fmt(low)}</span>
                  <div className="relative w-24 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                    {current != null && high > low && (
                      <div
                        className="absolute h-full bg-blue-500 rounded-full"
                        style={{ width: `${Math.min(100, Math.max(0, ((current - low) / (high - low)) * 100))}%` }}
                      />
                    )}
                  </div>
                  <span className="text-[10px] text-green-400 font-mono">{fmt(high)}</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  )
}
