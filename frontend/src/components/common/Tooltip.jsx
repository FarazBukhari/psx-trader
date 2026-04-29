/**
 * Tooltip — hover tooltip for column headers and info icons.
 * Usage: <Tooltip text="RSI explanation"><span>ⓘ</span></Tooltip>
 */

import { useState } from 'react'
import clsx from 'clsx'

export default function Tooltip({ text, children, placement = 'top' }) {
  const [visible, setVisible] = useState(false)

  const placementCls = {
    top:    'bottom-full left-1/2 -translate-x-1/2 mb-1.5',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-1.5',
    left:   'right-full top-1/2 -translate-y-1/2 mr-1.5',
    right:  'left-full top-1/2 -translate-y-1/2 ml-1.5',
  }[placement] || 'bottom-full left-1/2 -translate-x-1/2 mb-1.5'

  return (
    <span
      className="relative inline-flex items-center"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {visible && (
        <span
          className={clsx(
            'absolute z-50 w-max max-w-xs px-2.5 py-1.5 rounded',
            'bg-gray-800 border border-gray-700 text-gray-200 text-xs leading-snug',
            'pointer-events-none shadow-xl',
            placementCls,
          )}
        >
          {text}
        </span>
      )}
    </span>
  )
}

/** Convenience wrapper: shows a ⓘ icon that triggers the tooltip. */
export function InfoTip({ text }) {
  return (
    <Tooltip text={text}>
      <span className="ml-1 text-gray-600 hover:text-gray-400 cursor-help text-[10px]">ⓘ</span>
    </Tooltip>
  )
}
