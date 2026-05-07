/**
 * BacktestPanel — run form for single / presets / variants backtests.
 */

import { useState, useImperativeHandle, forwardRef } from 'react'
import clsx from 'clsx'
import { runBacktest } from '../../api/backtest'
import { useBacktestStore } from '../../store/useBacktestStore'
import Loader from '../common/Loader'
import ResultsTable from './ResultsTable'
import EquityChart from './EquityChart'
import StrategyGuide from './StrategyGuide'
import TradeLog from './TradeLog'

const MODES = [
  { id: 'single',  label: '⚡ Single',  desc: 'Custom config for one symbol' },
  { id: 'presets', label: '📋 Presets', desc: 'Run all 4 built-in strategies' },
  { id: 'variants',label: '🔁 Variants',desc: 'Compare custom parameter sets' },
]

const DEFAULT_CFG = {
  name: 'default',
  rsi_period: 14,
  rsi_oversold: 30,
  rsi_overbought: 70,
  sma_short: 5,
  sma_long: 20,
  stop_loss_pct: 5.0,
  change_pct_threshold: 3.0,
  position_size_pct: 1.0,
  starting_cash: 100000,
}

function CfgField({ label, name, value, onChange, min, max, step = 1 }) {
  return (
    <div className="flex flex-col gap-0.5">
      <label className="text-[10px] text-gray-600 uppercase tracking-wider">{label}</label>
      <input
        type="number"
        name={name}
        value={value}
        onChange={onChange}
        min={min}
        max={max}
        step={step}
        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200
                   focus:outline-none focus:border-blue-500 w-28"
      />
    </div>
  )
}

function ConfigEditor({ cfg, onChange }) {
  const set = (e) => onChange({ ...cfg, [e.target.name]: parseFloat(e.target.value) || e.target.value })
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4 bg-gray-800/50 rounded-lg border border-gray-700">
      <CfgField label="RSI Period"   name="rsi_period"          value={cfg.rsi_period}          onChange={set} min={2}   max={50} />
      <CfgField label="RSI Oversold" name="rsi_oversold"        value={cfg.rsi_oversold}        onChange={set} min={1}   max={49} />
      <CfgField label="RSI Overbought" name="rsi_overbought"    value={cfg.rsi_overbought}      onChange={set} min={51}  max={99} />
      <CfgField label="SMA Short"    name="sma_short"           value={cfg.sma_short}           onChange={set} min={2}   max={50} />
      <CfgField label="SMA Long"     name="sma_long"            value={cfg.sma_long}            onChange={set} min={5}   max={200} />
      <CfgField label="Stop Loss %"  name="stop_loss_pct"       value={cfg.stop_loss_pct}       onChange={set} min={0.5} max={30} step={0.5} />
      <CfgField label="Chg % Thresh" name="change_pct_threshold" value={cfg.change_pct_threshold} onChange={set} min={0.5} max={20} step={0.5} />
      <CfgField label="Position Size" name="position_size_pct"  value={cfg.position_size_pct}  onChange={set} min={0.1} max={1} step={0.1} />
      <CfgField label="Starting Cash" name="starting_cash"      value={cfg.starting_cash}      onChange={set} min={1000} step={1000} />
    </div>
  )
}

const DEFAULT_VARIANTS = [
  { ...DEFAULT_CFG, name: 'variant-1' },
  { ...DEFAULT_CFG, name: 'variant-2', rsi_oversold: 25, rsi_overbought: 75, sma_short: 10, sma_long: 30 },
]

const BacktestPanel = forwardRef(function BacktestPanel(props, ref) {
  const addRun    = useBacktestStore((s) => s.addRun)
  const [symbol,   setSymbol]   = useState('')
  const [mode,     setMode]     = useState('presets')
  const [cfg,      setCfg]      = useState({ ...DEFAULT_CFG })
  const [variants, setVariants] = useState(DEFAULT_VARIANTS.map((v) => ({ ...v })))
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [result,   setResult]   = useState(null)
  const [guideOpen, setGuideOpen] = useState(false)

  useImperativeHandle(ref, () => ({
    prefillSymbol: (sym) => {
      setSymbol(sym.toUpperCase())
      setResult(null)
      setError(null)
    },
  }))

  const updateVariant = (i, updated) =>
    setVariants((prev) => prev.map((v, idx) => idx === i ? updated : v))

  const addVariant = () =>
    setVariants((prev) => [
      ...prev,
      { ...DEFAULT_CFG, name: `variant-${prev.length + 1}` },
    ])

  const removeVariant = (i) =>
    setVariants((prev) => prev.length > 2 ? prev.filter((_, idx) => idx !== i) : prev)

  const handleRun = async (e) => {
    e.preventDefault()
    const sym = symbol.trim().toUpperCase()
    if (!sym) { setError('Symbol is required'); return }

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const payload = { symbol: sym, mode }
      if (mode === 'single')   payload.config   = cfg
      if (mode === 'variants') payload.variants = variants
      const data = await runBacktest(payload)
      setResult(data)
      addRun(data)
    } catch (err) {
      setError(err.message || 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  // Normalise result → list of rows for ResultsTable
  const resultRows = result
    ? result.mode === 'single'
      ? [result]
      : result.results || []
    : []

  const allSkipped   = resultRows.length > 0 && resultRows.every((r) => r.skipped)
  const equityCurve  = result?.equity_curve || null
  const startingCash = result?.starting_cash || cfg.starting_cash

  return (
    <div className="space-y-5">
      {/* ── Form ── */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-gray-200 uppercase tracking-wide">Run Backtest</h3>
          <button
            type="button"
            onClick={() => setGuideOpen(true)}
            title="Explain strategy parameters"
            className="w-5 h-5 rounded-full border border-gray-600 text-gray-500 hover:text-gray-200
                       hover:border-gray-400 transition text-[11px] font-bold leading-none flex items-center justify-center"
          >
            ?
          </button>
        </div>
        <form onSubmit={handleRun} className="space-y-4">
          {/* Symbol + Mode */}
          <div className="flex flex-wrap gap-4 items-end">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500 uppercase tracking-wide">Symbol</label>
              <input
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100
                           focus:outline-none focus:border-blue-500 w-36"
                placeholder="e.g. ENGRO"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                required
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500 uppercase tracking-wide">Mode</label>
              <div className="flex gap-1">
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setMode(m.id)}
                    title={m.desc}
                    className={clsx(
                      'px-3 py-2 rounded-lg text-xs font-bold transition border',
                      mode === m.id
                        ? 'bg-blue-600 text-white border-blue-500'
                        : 'bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-600',
                    )}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Single mode — one config */}
          {mode === 'single' && (
            <ConfigEditor cfg={cfg} onChange={setCfg} />
          )}

          {/* Variants mode — multiple named configs */}
          {mode === 'variants' && (
            <div className="space-y-3">
              {variants.map((v, i) => (
                <div key={i} className="rounded-lg border border-gray-700 overflow-hidden">
                  {/* Variant header */}
                  <div className="flex items-center gap-3 px-4 py-2 bg-gray-800/70 border-b border-gray-700">
                    <input
                      type="text"
                      value={v.name}
                      onChange={(e) => updateVariant(i, { ...v, name: e.target.value })}
                      placeholder={`variant-${i + 1}`}
                      className="bg-transparent text-xs font-semibold text-gray-300 focus:outline-none
                                 border-b border-transparent focus:border-gray-500 w-32"
                    />
                    <span className="text-[10px] text-gray-600 ml-auto">Variant {i + 1}</span>
                    {variants.length > 2 && (
                      <button
                        type="button"
                        onClick={() => removeVariant(i)}
                        className="text-[10px] text-gray-700 hover:text-red-500 transition ml-2"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <div className="p-4 bg-gray-800/30">
                    <ConfigEditor cfg={v} onChange={(updated) => updateVariant(i, updated)} />
                  </div>
                </div>
              ))}
              <button
                type="button"
                onClick={addVariant}
                className="text-xs text-gray-500 hover:text-gray-300 transition border border-dashed
                           border-gray-700 hover:border-gray-600 rounded-lg px-4 py-2 w-full"
              >
                + Add Variant
              </button>
            </div>
          )}

          {mode === 'presets' && (
            <div className="text-xs text-gray-600 bg-gray-800/50 rounded px-3 py-2 border border-gray-700">
              Runs 4 built-in strategy presets (Conservative, Default, Aggressive, Momentum) and compares results.
            </div>
          )}

          {error && (
            <div className="px-3 py-2 bg-red-900/40 border border-red-800 rounded text-xs text-red-300">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="px-6 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold
                       transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader size="sm" />}
            {loading ? 'Running…' : '▶ Run Backtest'}
          </button>
        </form>
      </div>

      {/* ── Results ── */}
      {result && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-bold text-gray-200 uppercase tracking-wide">
              Results — {result.symbol} ({result.mode})
            </h3>
            <span className="text-xs text-gray-600">
              {result.count != null ? `${result.count} strategies` : '1 run'}
            </span>
          </div>

          <ResultsTable results={resultRows} />

          {allSkipped && (
            <div className="px-4 py-3 bg-yellow-900/20 border border-yellow-800/50 rounded-lg text-xs text-yellow-300 space-y-1">
              <div className="font-semibold">No historical data found for {result.symbol}</div>
              <div className="text-yellow-500">
                Fetch EOD history first — run this command from the <code className="bg-black/30 px-1 rounded">backend/</code> directory:
              </div>
              <code className="block bg-black/40 px-3 py-2 rounded font-mono text-yellow-200 mt-1">
                python -m scripts.fetch_historical --symbols {result.symbol}
              </code>
              <div className="text-yellow-600 pt-0.5">
                Or trigger via API: <code className="bg-black/30 px-1 rounded">POST /api/system/fetch-historical?symbols={result.symbol}</code>
              </div>
            </div>
          )}

          {/* Single mode — equity curve + trade log */}
          {result.mode === 'single' && (
            <>
              {equityCurve?.length > 1 && (
                <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                  <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">Equity Curve</div>
                  <EquityChart
                    equityCurve={equityCurve}
                    startingCash={startingCash}
                    label={result.strategy || result.symbol}
                  />
                </div>
              )}
              {result.trade_log?.length > 0 && (
                <TradeLog tradeLog={result.trade_log} label={result.strategy} />
              )}
            </>
          )}

          {/* Multi-mode — equity curves + trade log per strategy */}
          {result.results?.length > 0 && (
            <div className="space-y-4">
              {result.results.map((r, i) => (
                (r.equity_curve?.length > 1 || r.trade_log?.length > 0) && (
                  <div key={i} className="space-y-3">
                    {r.equity_curve?.length > 1 && (
                      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                        <EquityChart
                          equityCurve={r.equity_curve}
                          startingCash={r.starting_cash}
                          label={r.strategy}
                        />
                      </div>
                    )}
                    {r.trade_log?.length > 0 && (
                      <TradeLog tradeLog={r.trade_log} label={r.strategy} />
                    )}
                  </div>
                )
              ))}
            </div>
          )}
        </div>
      )}

      <StrategyGuide open={guideOpen} onClose={() => setGuideOpen(false)} />
    </div>
  )
})

export default BacktestPanel
