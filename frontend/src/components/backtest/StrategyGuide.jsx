/**
 * StrategyGuide — modal explaining every strategy parameter.
 * Opened by clicking the ? button in BacktestPanel.
 */

const PARAMS = [
  {
    name: 'RSI Period',
    key:  'rsi_period',
    desc: 'How many days of price data are used to calculate the RSI. A longer period smooths out noise but reacts more slowly to new trends. A shorter period is more sensitive but can generate false signals.',
    example: '14 days is the standard. Try 7 for a faster-reacting signal, 21 for a calmer one.',
  },
  {
    name: 'RSI Oversold',
    key:  'rsi_oversold',
    desc: 'The RSI threshold below which a stock is considered oversold — meaning it may have been sold off too aggressively and a bounce is possible. The strategy treats a cross above this level as a potential buy signal.',
    example: 'Default 30. Lower (e.g. 25) = only buy at extreme dips, fewer but higher-conviction trades. Higher (e.g. 40) = buy earlier, more trades but more false positives.',
  },
  {
    name: 'RSI Overbought',
    key:  'rsi_overbought',
    desc: 'The RSI threshold above which a stock is considered overbought — it may have run up too fast. A cross below this level can trigger a sell signal.',
    example: 'Default 70. Higher (e.g. 75) = let winners run longer before selling. Lower (e.g. 65) = exit earlier, lock in gains sooner.',
  },
  {
    name: 'SMA Short',
    key:  'sma_short',
    desc: 'The short-period Simple Moving Average — the average closing price over the last N days. It tracks recent price momentum. When this crosses above the long SMA, it suggests upward momentum (bullish crossover).',
    example: 'Default 5. A 5-day SMA reflects roughly one trading week of data.',
  },
  {
    name: 'SMA Long',
    key:  'sma_long',
    desc: 'The long-period Simple Moving Average — a slower, smoother trend line. The gap between short and long SMA shows how much momentum has built up. A bullish crossover (short above long) is a buy signal; bearish (short below long) is a sell signal.',
    example: 'Default 20. A 20-day SMA is roughly one trading month. Wider spread between short and long = more lag, fewer signals.',
  },
  {
    name: 'Stop Loss %',
    key:  'stop_loss_pct',
    desc: 'The maximum loss you\'re willing to tolerate from your entry price before the position is force-closed. This caps downside risk on any single trade. The position is sold if the price drops this percentage below your average buy price.',
    example: '5% means: if you buy at PKR 100 and the price falls to PKR 95, the position is exited automatically.',
  },
  {
    name: 'CHG % Threshold',
    key:  'change_pct_threshold',
    desc: 'The minimum single-day price change percentage required before the momentum sub-signal fires. This filters out small day-to-day noise and only reacts to meaningful price moves.',
    example: '3% means a stock must move at least 3% in a day to trigger the momentum component. Lower values = more reactive, higher = more selective.',
  },
  {
    name: 'Position Size',
    key:  'position_size_pct',
    desc: 'The fraction of your available cash to deploy on each trade. 1.0 means go all-in with all cash. 0.5 means use half your cash, keeping the rest in reserve.',
    example: '1.0 = maximum exposure (higher reward, higher risk). 0.25 = spread risk across potential multiple trades.',
  },
  {
    name: 'Starting Cash',
    key:  'starting_cash',
    desc: 'The hypothetical capital the simulation starts with. All returns, P&L, and equity values are expressed relative to this amount. It does not affect the strategy logic — only the absolute PKR figures in the results.',
    example: 'Set this to your actual portfolio value to see real-world equivalent figures.',
  },
]

export default function StrategyGuide({ open, onClose }) {
  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

      {/* Panel */}
      <div
        className="relative z-10 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl
                   w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div>
            <h2 className="text-sm font-bold text-gray-100 uppercase tracking-wider">Strategy Parameters</h2>
            <p className="text-xs text-gray-500 mt-0.5">What each setting controls and how to tune it</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-600 hover:text-gray-300 transition text-lg leading-none px-1"
          >
            ✕
          </button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto px-6 py-4 space-y-5">
          {PARAMS.map((p) => (
            <div key={p.key} className="flex gap-4">
              <div className="w-1 rounded-full bg-gray-700 flex-shrink-0 self-stretch" />
              <div className="space-y-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-semibold text-gray-100">{p.name}</span>
                  <code className="text-[10px] text-gray-600 font-mono">{p.key}</code>
                </div>
                <p className="text-xs text-gray-400 leading-relaxed">{p.desc}</p>
                <p className="text-[11px] text-gray-600 leading-relaxed">
                  <span className="text-gray-500 font-medium">e.g. </span>{p.example}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-800">
          <p className="text-[11px] text-gray-600">
            RSI = Relative Strength Index (momentum oscillator, 0–100). SMA = Simple Moving Average (trend filter).
            Signals require <span className="text-gray-500">both</span> RSI and SMA conditions to align before a trade is entered.
          </p>
        </div>
      </div>
    </div>
  )
}
