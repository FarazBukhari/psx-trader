/**
 * Badge — signal type badge with semantic colour.
 * variant: 'BUY' | 'SELL' | 'FORCE_SELL' | 'HOLD' | string
 */

import clsx from 'clsx'

const VARIANTS = {
  BUY:        'bg-green-500/20 text-green-300 border-green-600',
  SELL:       'bg-orange-500/20 text-orange-300 border-orange-600',
  FORCE_SELL: 'bg-red-500/30 text-red-300 border-red-500 animate-pulse',
  HOLD:       'bg-gray-700/50 text-gray-400 border-gray-600',
}

export default function Badge({ variant = 'HOLD', label, pulse = false, className }) {
  const v = (variant || 'HOLD').toUpperCase()
  const cls = VARIANTS[v] || 'bg-gray-700/50 text-gray-400 border-gray-600'
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold border tracking-wide',
        cls,
        pulse && 'animate-pulse',
        className,
      )}
    >
      {label ?? v}
    </span>
  )
}
