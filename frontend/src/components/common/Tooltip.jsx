/**
 * Tooltip — hover tooltip that renders via a React portal so it always
 * appears above sticky headers and overflow-clipped containers.
 *
 * Usage: <Tooltip text="RSI explanation"><span>ⓘ</span></Tooltip>
 */

import { useState, useRef } from 'react'
import { createPortal } from 'react-dom'

export default function Tooltip({ text, children }) {
  const [pos, setPos] = useState(null)
  const ref = useRef(null)

  const handleMouseEnter = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect()
      setPos({ x: r.left + r.width / 2, y: r.top })
    }
  }

  return (
    <span
      ref={ref}
      className="inline-flex items-center"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={() => setPos(null)}
    >
      {children}
      {pos && createPortal(
        <span
          style={{
            position:  'fixed',
            left:      pos.x,
            top:       pos.y - 8,
            transform: 'translate(-50%, -100%)',
            zIndex:    9999,
            pointerEvents: 'none',
            maxWidth:  '18rem',
          }}
          className="px-2.5 py-1.5 rounded bg-gray-800 border border-gray-700 text-gray-200 text-xs leading-snug shadow-xl whitespace-normal"
        >
          {text}
        </span>,
        document.body,
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
