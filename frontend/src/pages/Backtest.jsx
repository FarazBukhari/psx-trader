/**
 * Backtest page — strategy testing UI.
 */

/**
 * Backtest page — strategy testing UI.
 */

import { useRef } from 'react'
import BacktestPanel     from '../components/backtest/BacktestPanel'
import BacktestHistory   from '../components/backtest/BacktestHistory'
import PortfolioSignals  from '../components/backtest/PortfolioSignals'

export default function Backtest() {
  const panelRef  = useRef(null)   // BacktestPanel imperative handle (prefillSymbol)
  const scrollRef = useRef(null)   // div anchor for scrollIntoView

  function handleBacktest(symbol) {
    panelRef.current?.prefillSymbol(symbol)
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <div className="px-5 py-5 max-w-screen-xl mx-auto w-full space-y-5">
      <div>
        <h2 className="text-sm font-bold text-gray-300 uppercase tracking-widest">Strategy Backtester</h2>
        <p className="text-xs text-gray-600 mt-0.5">
          Test strategies against historical price data stored in the database.
        </p>
      </div>
      <PortfolioSignals onBacktest={handleBacktest} />
      <div ref={scrollRef}>
        <BacktestPanel ref={panelRef} />
      </div>
      <BacktestHistory />
    </div>
  )
}
