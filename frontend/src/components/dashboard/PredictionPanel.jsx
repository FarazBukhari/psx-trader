/**
 * PredictionPanel — expanded row detail showing prediction data.
 * Renders inside the SignalTable when a row is expanded.
 */

import clsx from 'clsx'
import Badge from '../common/Badge'

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

function Stat({ label, value, className }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider">{label}</span>
      <span className={clsx('text-sm font-semibold', className)}>{value}</span>
    </div>
  )
}

export default function PredictionPanel({ signal }) {
  const pred = signal?.prediction
  if (!pred) {
    return (
      <div className="px-4 py-3 text-xs text-gray-600 italic">
        No prediction available yet — price history accumulating.
      </div>
    )
  }

  const dir = pred.direction || 'neutral'
  const conf = pred.confidence != null ? `${(pred.confidence * 100).toFixed(0)}%` : '—'
  const riskCls = RISK_COLOR[pred.risk_level] || 'text-gray-400'

  return (
    <div className="px-5 py-4 bg-gray-900/70 border-t border-gray-700/50 grid grid-cols-2 md:grid-cols-4 gap-5">
      {/* Direction + confidence */}
      <div className="col-span-2 md:col-span-1 flex items-center gap-3">
        <span className={clsx('text-3xl font-black', DIR_COLOR[dir])}>
          {DIR_ICON[dir]}
        </span>
        <div>
          <div className={clsx('text-base font-bold uppercase', DIR_COLOR[dir])}>
            {dir}
          </div>
          <div className="text-xs text-gray-500">Direction</div>
        </div>
        <div className="ml-4">
          <div className="text-base font-bold text-white">{conf}</div>
          <div className="text-xs text-gray-500">Confidence</div>
        </div>
      </div>

      {/* Stats grid */}
      <div className="flex gap-6 items-start">
        <Stat
          label="Hold Days"
          value={pred.hold_days != null ? `~${pred.hold_days}d` : '—'}
          className="text-gray-200"
        />
        <Stat
          label="Risk"
          value={pred.risk_level || '—'}
          className={riskCls}
        />
        <Stat
          label="Reward/Risk"
          value={pred.reward_risk != null ? pred.reward_risk.toFixed(2) : '—'}
          className={
            pred.reward_risk >= 2 ? 'text-green-400'
            : pred.reward_risk >= 1 ? 'text-yellow-400'
            : 'text-red-400'
          }
        />
      </div>

      {/* Signal */}
      <div className="flex items-start gap-2">
        <div>
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Signal</div>
          <Badge variant={signal.signal} />
        </div>
        <div className="ml-4">
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Score</div>
          <span className="text-sm font-mono text-gray-300">
            {signal.action_score != null ? Math.round(signal.action_score) : '—'}
          </span>
        </div>
      </div>

      {/* Basis */}
      {pred.basis?.length > 0 && (
        <div className="col-span-2 md:col-span-4">
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1.5">Basis</div>
          <div className="flex flex-wrap gap-1.5">
            {pred.basis.map((b, i) => (
              <span
                key={i}
                className="px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-xs text-gray-300"
              >
                {b}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Sources */}
      {signal.signal_sources?.length > 0 && (
        <div className="col-span-2 md:col-span-4">
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1.5">Signal Sources</div>
          <div className="flex flex-wrap gap-1.5">
            {signal.signal_sources.map((src, i) => (
              <span
                key={i}
                className="px-2 py-0.5 rounded bg-blue-900/30 border border-blue-800/50 text-xs text-blue-300"
              >
                {src}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
