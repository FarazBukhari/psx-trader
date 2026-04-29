/**
 * Tabs — top-level page navigation tabs.
 */

import clsx from 'clsx'
import { useUIStore } from '../../store/useUIStore'

const TABS = [
  { id: 'dashboard', label: '📊 Dashboard' },
  { id: 'portfolio', label: '💼 Portfolio' },
  { id: 'backtest',  label: '📈 Backtest' },
]

export default function Tabs() {
  const activeTab = useUIStore((s) => s.activeTab)
  const setTab    = useUIStore((s) => s.setTab)

  return (
    <div className="flex gap-1">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          onClick={() => setTab(tab.id)}
          className={clsx(
            'px-4 py-2 text-sm font-semibold rounded-t transition-colors',
            activeTab === tab.id
              ? 'bg-gray-900 text-white border-b-2 border-blue-500'
              : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/50',
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
