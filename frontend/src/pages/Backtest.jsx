/**
 * Backtest page — strategy testing UI.
 */

import BacktestPanel   from '../components/backtest/BacktestPanel'
import BacktestHistory from '../components/backtest/BacktestHistory'

export default function Backtest() {
  return (
    <div className="px-5 py-5 max-w-screen-xl mx-auto w-full space-y-5">
      <div>
        <h2 className="text-sm font-bold text-gray-300 uppercase tracking-widest">Strategy Backtester</h2>
        <p className="text-xs text-gray-600 mt-0.5">
          Test strategies against historical price data stored in the database.
        </p>
      </div>
      <BacktestPanel />
      <BacktestHistory />
    </div>
  )
}
