/**
 * BacktestPanel — run form for single / presets / variants backtests.
 */

import { useState } from 'react'
import clsx from 'clsx'
import { runBacktest } from '../../api/backtest'
import { useBacktestStore } from '../../store/useBacktestStore'
import Loader from '../common/Loader'
import ResultsTable from './ResultsTable'
import EquityChart from './EquityChart'

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
      <CfgField label="Starting Cash" name="starting_cash"      value={cfg.starting_cash}      onChange={set} min={1000} step={10000} />
    </div>
  )
}

export default function BacktestPanel() {
  const addRun    = useBacktestStore((s) => s.addRun)
  const [symbol,  setSymbol]  = useState('')
  const [mode,    setMode]    = useState('presets')
  const [cfg,     setCfg]     = useState({ ...DEFAULT_CFG })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const [result,  setResult]  = useState(null)

  const handleRun = async (e) => {
    e.preventDefault()
    const sym = symbol.trim().toUpperCase()
    if (!sym) { setError('Symbol is required'); return }

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const payload = { symbol: sym, mode }
      if (mode === 'single') payload.config = cfg
      // presets & variants: backend uses built-in configs
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

  // Pick equity curve for single mode
  const equityCurve  = result?.equity_curve || null
  const startingCash = result?.starting_cash || cfg.starting_cash

  return (
    <div className="space-y-5">
      {/* ── Form ── */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
        <h3 className="text-sm font-bold text-gray-200 mb-4 uppercase tracking-wide">Run Backtest</h3>
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

          {/* Config editor for single + variants */}
          {(mode === 'single' || mode === 'variants') && (
            <ConfigEditor cfg={cfg} onChange={setCfg} />
          )}

          {mode === 'presets' && (
            <div className="text-xs text-gray-600 bg-gray-800/50 rounded px-3 py-2 border border-gray-700">
              Runs 4 built-in strategy presets (Conservative, Balanced, Aggressive, Momentum) and compares results.
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

          {/* Equity curve (single mode only) */}
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

          {/* Multi-mode equity curves */}
          {result.results?.length > 0 && result.results.some((r) => r.equity_curve?.length > 1) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {result.results.map((r, i) => (
                r.equity_curve?.length > 1 && (
                  <div key={i} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                    <EquityChart
                      equityCurve={r.equity_curve}
                      startingCash={r.starting_cash}
                      label={r.strategy}
                    />
                  </div>
                )
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
