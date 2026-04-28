/**
 * SignalBadge — colored pill for BUY / SELL / HOLD / FORCE_SELL
 */

const STYLES = {
  BUY:        'bg-green-900/60 text-green-300 border border-green-700 shadow-green-900/40',
  SELL:       'bg-red-900/60 text-red-300 border border-red-700 shadow-red-900/40',
  HOLD:       'bg-gray-800 text-gray-400 border border-gray-700',
  FORCE_SELL: 'bg-red-600 text-white border border-red-400 animate-pulse shadow-red-600/60',
}

const ICONS = {
  BUY:        '▲',
  SELL:       '▼',
  HOLD:       '●',
  FORCE_SELL: '✖',
}

export default function SignalBadge({ signal, changed }) {
  const cls = STYLES[signal] || STYLES.HOLD
  const ico = ICONS[signal] || '?'
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold shadow-sm ${cls} ${changed ? 'ring-2 ring-yellow-400 ring-offset-1 ring-offset-gray-900' : ''}`}>
      {ico} {signal?.replace('_', ' ')}
    </span>
  )
}
