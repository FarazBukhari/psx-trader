/**
 * PortfolioSignals — live signal status for each held position.
 *
 * Shows the signal engine's current read on every holding: signal badge,
 * RSI, stop-loss trigger price, and key thresholds. A "Backtest" button
 * pre-fills the symbol in BacktestPanel so the user can run history in
 * one click.
 *
 * This is NOT a prediction — it shows what the strategy says RIGHT NOW
 * based on live data, and what price levels would change that opinion.
 */

import { useEffect } from 'react'
import clsx from 'clsx'
import { usePortfolioStore } from '../../store/usePortfolioStore'
import { useMarketStore }    from '../../store/useMarketStore'

// Default strategy thresholds — matches strategy.json defaults.
// The signal engine uses these; we mirror them here for the threshold display.
const RSI_OVERSOLD   = 30
const RSI_OVERBOUGHT = 70
const STOP_LOSS_PCT  = 5.0   // %

const SIGNAL_STYLES = {
  BUY:        { badge: 'bg-green-900/50 text-green-300 border-green-700',  border: 'border-green-800/40'  },
  SELL:       { badge: 'bg-orange-900/50 text-orange-300 border-orange-700', border: 'border-orange-800/40' },
  FORCE_SELL: { badge: 'bg-red-900/50 text-red-300 border-red-700',        border: 'border-red-800/40'    },
  HOLD:       { badge: 'bg-gray-800 text-gray-400 border-gray-700',        border: 'border-gray-800/40'   },
}

function fmt(v, d = 2) {
  return v != null ? Number(v).toFixed(d) : '—'
}
function fmtPKR(v) {
  return v != null
    ? `PKR ${Number(v).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : '—'
}

function RsiBar({ rsi }) {
  if (rsi == null) return <span className="text-gray-600 text-xs">—</span>
  const pct   = Math.min(100, Math.max(0, rsi))
  const color = rsi <= RSI_OVERSOLD  ? 'bg-green-500'
              : rsi >= RSI_OVERBOUGHT ? 'bg-orange-500'
              : 'bg-blue-500'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className={clsx('text-xs font-mono tabular-nums',
        rsi <= RSI_OVERSOLD  ? 'text-green-400' :
        rsi >= RSI_OVERBOUGHT ? 'text-orange-400' : 'text-gray-300'
      )}>
        {fmt(rsi, 1)}
      </span>
    </div>
  )
}

export default function PortfolioSignals({ onBacktest }) {
  const portfolio  = usePortfolioStore((s) => s.portfolio)
  const loading    = usePortfolioStore((s) => s.loading)
  const fetchPort  = usePortfolioStore((s) => s.fetch)
  const getSignal  = useMarketStore((s) => s.getSignal)
  const connected  = useMarketStore((s) => s.connected)

  // Fetch portfolio on mount if not already loaded
  useEffect(() => {
    if (!portfolio) fetchPort()
  }, [])

  const positions = portfolio?.positions ?? []

  // Loading skeleton
  if (!portfolio || loading) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-3 animate-pulse">
        <div className="h-3 w-48 bg-gray-800 rounded" />
        <div className="h-2 w-72 bg-gray-800/60 rounded" />
        <div className="h-20 bg-gray-800/40 rounded-lg" />
      </div>
    )
  }

  if (!positions.length) return null

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="px-5 py-3 border-b border-gray-800 flex items-center justify-between">
        <div>
          <span className="text-xs font-bold text-gray-300 uppercase tracking-wider">
            My Portfolio — Live Signals
          </span>
          <p className="text-[11px] text-gray-600 mt-0.5">
            What the strategy engine says right now for each holding. Not a prediction — reflects current indicators only.
          </p>
        </div>
        <div className={clsx(
          'text-[10px] px-2 py-0.5 rounded border font-semibold',
          connected
            ? 'text-green-400 border-green-800 bg-green-900/20'
            : 'text-gray-600 border-gray-700 bg-gray-800/40',
        )}>
          {connected ? 'LIVE' : 'OFFLINE'}
        </div>
      </div>

      {/* Positions */}
      <div className="divide-y divide-gray-800/50">
        {positions.map((pos) => {
          const live     = getSignal(pos.symbol)(useMarketStore.getState())
          const sig      = live?.signal ?? 'HOLD'
          const styles   = SIGNAL_STYLES[sig] ?? SIGNAL_STYLES.HOLD
          const price    = live?.current ?? pos.current_price ?? pos.avg_buy_price
          const rsi      = live?.rsi
          const sources  = live?.signal_sources ?? []

          // Key price levels
          const stopLossPrice  = pos.avg_buy_price * (1 - STOP_LOSS_PCT / 100)
          const unrealizedPL   = (price - pos.avg_buy_price) * pos.shares
          const unrealizedPct  = ((price - pos.avg_buy_price) / pos.avg_buy_price) * 100
          const plPositive     = unrealizedPL >= 0

          // RSI distance to thresholds
          const rsiToSell    = rsi != null ? Math.max(0, RSI_OVERBOUGHT - rsi).toFixed(1) : null
          const rsiToBuy     = rsi != null ? Math.max(0, rsi - RSI_OVERSOLD).toFixed(1)   : null

          // SMA crossover status
          const sma5   = live?.sma5
          const sma20  = live?.sma20
          const smaStatus = sma5 != null && sma20 != null
            ? sma5 > sma20 ? 'Bullish (fast > slow)' : 'Bearish (fast < slow)'
            : null

          return (
            <div key={pos.symbol} className={clsx(
              'px-5 py-4 border-l-4',
              styles.border,
              sig === 'BUY'        ? 'border-l-green-700'  :
              sig === 'SELL'       ? 'border-l-orange-700' :
              sig === 'FORCE_SELL' ? 'border-l-red-700'    :
                                     'border-l-gray-700',
            )}>
              {/* Row 1: symbol + signal + P&L + backtest button */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-base font-bold text-gray-100">{pos.symbol}</span>
                  <span className={clsx('px-1.5 py-0.5 rounded text-[10px] font-bold border', styles.badge)}>
                    {sig.replace('_', ' ')}
                  </span>
                  {sources.length > 0 && (
                    <span className="text-[10px] text-gray-600">
                      via {sources.join(', ')}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => onBacktest(pos.symbol)}
                  className="text-[11px] px-3 py-1 rounded border border-blue-800 text-blue-400
                             hover:bg-blue-900/30 transition font-semibold"
                >
                  Backtest ↗
                </button>
              </div>

              {/* Row 2: metrics grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 text-xs">

                {/* Current price + P&L */}
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">Price</div>
                  <div className="font-mono text-gray-200">{fmtPKR(price)}</div>
                  <div className={clsx('text-[10px] font-mono mt-0.5', plPositive ? 'text-green-400' : 'text-red-400')}>
                    {plPositive ? '+' : ''}{fmt(unrealizedPct, 2)}% on {pos.shares} shares
                  </div>
                </div>

                {/* Avg cost + stop-loss */}
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">Avg Cost</div>
                  <div className="font-mono text-gray-200">{fmtPKR(pos.avg_buy_price)}</div>
                  <div className="text-[10px] text-red-500/70 font-mono mt-0.5">
                    Stop-loss @ {fmtPKR(stopLossPrice)}
                  </div>
                </div>

                {/* RSI */}
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">RSI (14)</div>
                  <RsiBar rsi={rsi} />
                  {rsi != null && (
                    <div className="text-[10px] text-gray-600 mt-1">
                      {rsi < RSI_OVERSOLD
                        ? <span className="text-green-500">Oversold — buy signal active</span>
                        : rsi > RSI_OVERBOUGHT
                        ? <span className="text-orange-500">Overbought — sell signal active</span>
                        : <span>{rsiToSell} pts to sell · {rsiToBuy} pts to buy</span>
                      }
                    </div>
                  )}
                </div>

                {/* SMA crossover */}
                <div>
                  <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">SMA Crossover</div>
                  {smaStatus
                    ? <div className={clsx('text-xs font-semibold',
                        smaStatus.startsWith('Bull') ? 'text-green-400' : 'text-orange-400'
                      )}>{smaStatus}</div>
                    : <div className="text-gray-600">—</div>
                  }
                  {sma5 != null && sma20 != null && (
                    <div className="text-[10px] text-gray-600 font-mono mt-0.5">
                      SMA5 {fmt(sma5, 2)} · SMA20 {fmt(sma20, 2)}
                    </div>
                  )}
                </div>

              </div>

              {/* Row 3: plain-English summary */}
              <div className="mt-3 text-[11px] text-gray-500 bg-gray-800/30 rounded px-3 py-2">
                {sig === 'BUY'        && `Strategy says BUY — consider adding to this position. Sell would trigger above RSI ${RSI_OVERBOUGHT} or if SMA5 crosses below SMA20.`}
                {sig === 'SELL'       && `Strategy says SELL — consider reducing or exiting this position.`}
                {sig === 'FORCE_SELL' && `⚠ Stop-loss triggered — price has fallen ${STOP_LOSS_PCT}% below average cost. Strategy recommends exiting immediately.`}
                {sig === 'HOLD'       && `No strong signal yet. Watching for RSI to drop below ${RSI_OVERSOLD} (buy) or rise above ${RSI_OVERBOUGHT} (sell), and SMA crossover confirmation.`}
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer note */}
      <div className="px-5 py-2.5 border-t border-gray-800 bg-gray-900/50">
        <p className="text-[10px] text-gray-700">
          Signals update every ~15 seconds from live market data. RSI thresholds: buy ≤{RSI_OVERSOLD}, sell ≥{RSI_OVERBOUGHT}. Stop-loss: {STOP_LOSS_PCT}% below avg cost.
          Click <span className="text-gray-500">Backtest ↗</span> to see how the strategy would have traded this symbol historically.
        </p>
      </div>
    </div>
  )
}
