/**
 * PSX Trader — App root.
 * Boots WS connection once and renders the active tab page.
 */

import { useEffect }    from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import { useUIStore }   from './store/useUIStore'
import Header           from './components/layout/Header'
import Dashboard        from './pages/Dashboard'
import Portfolio        from './pages/Portfolio'
import Backtest         from './pages/Backtest'

function Pages() {
  const tab = useUIStore((s) => s.activeTab)
  return (
    <main className="flex-1 overflow-auto">
      {tab === 'dashboard' && <Dashboard />}
      {tab === 'portfolio' && <Portfolio />}
      {tab === 'backtest'  && <Backtest />}
    </main>
  )
}

function WSBoot() {
  // Mount WS at the root so it stays alive across tab switches
  useWebSocket()
  return null
}

function KeyboardShortcuts() {
  const setTab = useUIStore((s) => s.setTab)
  useEffect(() => {
    function handler(e) {
      // Skip when typing in an input / select / textarea
      const tag = document.activeElement?.tagName
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return
      if (e.key === 'b' || e.key === 'B') {
        setTab('portfolio')
        setTimeout(() => document.getElementById('trade-qty-input')?.focus(), 60)
      } else if (e.key === 's' || e.key === 'S') {
        setTab('portfolio')
        setTimeout(() => document.getElementById('trade-qty-input')?.focus(), 60)
      } else if (e.key === 'Enter') {
        document.querySelector('[data-confirm-btn]')?.click()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [setTab])
  return null
}

export default function App() {
  return (
    <div className="min-h-screen flex flex-col bg-gray-950 text-gray-100">
      <WSBoot />
      <KeyboardShortcuts />
      <Header />
      <Pages />
      <footer className="px-5 py-2 border-t border-gray-800/60 text-[10px] text-gray-800 text-center">
        PSX Trader — informational only · not financial advice
      </footer>
    </div>
  )
}
