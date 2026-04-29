/**
 * Loader — spinner + optional message. Used in loading states everywhere.
 * size: 'sm' | 'md' | 'lg'
 */

import clsx from 'clsx'

const SIZES = {
  sm: 'w-4 h-4 border-2',
  md: 'w-6 h-6 border-2',
  lg: 'w-8 h-8 border-2',
}

export default function Loader({ size = 'md', message, className }) {
  return (
    <div className={clsx('flex items-center gap-2 text-gray-500', className)}>
      <span
        className={clsx(
          'rounded-full border-gray-700 border-t-blue-400 animate-spin',
          SIZES[size] || SIZES.md,
        )}
      />
      {message && <span className="text-xs">{message}</span>}
    </div>
  )
}

/** Full-area centered loader — for page-level loading states */
export function PageLoader({ message = 'Loading…' }) {
  return (
    <div className="flex items-center justify-center h-48 w-full">
      <Loader size="lg" message={message} />
    </div>
  )
}
