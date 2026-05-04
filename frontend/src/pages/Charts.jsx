/**
 * Charts — per-symbol price chart with 1D / 1W / 1M / 1Y timeframes.
 *
 * Data source: GET /api/history/stats (symbol list + tick counts)
 *              GET /api/history/{symbol}?n=2000&since={unix_ts}
 */

import { useState, useEffect, useCallback } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts'
import { api } from '../api/client'

// ── Timeframe config ─────────────────────────────────────────────────────────

const TIMEFRAMES = [
  { id: '1D', label: '1D', seconds: 24 * 3600 },
  { id: '1W', label: '1W', seconds: 7 * 24 * 3600 },
  { id: '1M', label: '1M', seconds: 30 * 24 * 3600 },
  { id: '1Y', label: '1Y', seconds: 365 * 24 * 3600 },
]

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtPrice(v) {
  return `PKR ${Number(v).toLocaleString('en-PK', { maximumFractionDigits: 2 })}`
}

function fmtAxis(v) {
  if (v >= 1000) return `${(v / 1000).toFixed(1)}K`
  return v.toFixed(0)
}

function fmtTimestamp(ts, tfId) {
  const d = new Date(ts * 1000)
  if (tfId === '1D') {
    return d.toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' })
  }
  if (tfId === '1W') {
    return d.toLocaleDateString('en-PK', { weekday: 'short', day: 'numeric' })
  }
  return d.toLocaleDateString('en-PK', { day: 'numeric', month: 'short' })
}

function pctChange(first, last) {
  if (!first || first === 0) return null
  return ((last - first) / first * 100)
}

// ── Custom tooltip ────────────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label, tfId }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">{fmtTimestamp(label, tfId)}</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        {d.open  != null && <><span className="text-gray-500">Open</span>  <span className="text-right font-mono">{d.open.toFixed(2)}</span></>}
        {d.high  != null && <><span className="text-gray-500">High</span>  <span className="text-right font-mono text-green-400">{d.high.toFixed(2)}</span></>}
        {d.low   != null && <><span className="text-gray-500">Low</span>   <span className="text-right font-mono text-red-400">{d.low.toFixed(2)}</span></>}
        <span className="text-gray-500">Close</span><span className="text-right font-mono text-white">{d.close.toFixed(2)}</span>
        {d.volume != null && <><span className="text-gray-500">Vol</span>  <span className="text-right font-mono text-blue-300">{Number(d.volume).toLocaleString()}</span></>}
      </div>
    </div>
  )
}

// ── Mini stat card ────────────────────────────────────────────────────────────

function Stat({ label, value, sub, color }) {
  return (
    <div className="bg-gray-900 rounded-lg px-4 py-2.5 border border-gray-800">
      <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`text-sm font-mono font-bold ${color || 'text-white'}`}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600 mt-0.5">{sub}</div>}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Charts() {
  const [symbols,    setSymbols]    = useState([])   // from /api/history/stats
  const [selected,   setSelected]   = useState(null)
  const [tf,         setTf]         = useState('1D')
  const [chartData,  setChartData]  = useState([])
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState(null)
  const [statsMap,   setStatsMap]   = useState({})   // symbol → {ticks, first_at, last_at}

  // Load symbol list from stats endpoint
  useEffect(() => {
    api('/api/history/stats')
      .then((res) => {
        const syms = Object.keys(res.data || {}).sort()
        setSymbols(syms)
        setStatsMap(res.data || {})
        if (syms.length > 0 && !selected) setSelected(syms[0])
      })
      .catch((e) => setError(e.message))
  }, [])

  // Fetch chart data when symbol or timeframe changes
  const loadChart = useCallback(async (sym, tfId) => {
    if (!sym) return
    setLoading(true)
    setError(null)
    try {
      const tfCfg = TIMEFRAMES.find((t) => t.id === tfId)
      const since = Math.floor(Date.now() / 1000) - tfCfg.seconds
      const res   = await api(`/api/history/${sym}?n=2000&since=${since}`)
      setChartData(res.data || [])
    } catch (e) {
      if (e.status === 404) {
        setChartData([])
        setError(`No data for ${sym} in this timeframe. Run fetch-historical or wait for live ticks.`)
      } else {
        setError(e.message)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected) loadChart(selected, tf)
  }, [selected, tf, loadChart])

  // Derived chart stats
  const closes   = chartData.map((d) => d.close).filter(Boolean)
  const highs    = chartData.map((d) => d.high).filter(Boolean)
  const lows     = chartData.map((d) => d.low).filter(Boolean)
  const first    = closes[0]
  const last     = closes[closes.length - 1]
  const chg      = pctChange(first, last)
  const isUp     = chg == null ? true : chg >= 0
  const high52   = highs.length ? Math.max(...highs) : null
  const low52    = lows.length  ? Math.min(...lows)  : null
  const avgVol   = chartData.length
    ? Math.round(chartData.reduce((s, d) => s + (d.volume || 0), 0) / chartData.length)
    : null

  const gradId  = isUp ? 'grad-up' : 'grad-dn'
  const stroke  = isUp ? '#22c55e' : '#f97316'
  const fillTop = isUp ? '#22c55e33' : '#f9731633'

  const stats = statsMap[selected] || {}

  return (
    <div className="p-5 space-y-5">
      {/* ── Header row ── */}
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-bold text-white">📉 Price Charts</h2>
        <span className="text-xs text-gray-600">—</span>

        {/* Symbol selector */}
        <div className="flex flex-wrap gap-1.5 flex-1">
          {symbols.length === 0 && (
            <span className="text-xs text-gray-600">Loading symbols…</span>
          )}
          {symbols.map((sym) => (
            <button
              key={sym}
              onClick={() => setSelected(sym)}
              className={`px-2.5 py-0.5 rounded text-xs font-bold border transition ${
                selected === sym
                  ? 'bg-blue-600 border-blue-500 text-white'
                  : 'bg-gray-900 border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
              }`}
            >
              {sym}
            </button>
          ))}
        </div>

        {/* Timeframe buttons */}
        <div className="flex gap-1 bg-gray-900 border border-gray-700 rounded-lg p-0.5">
          {TIMEFRAMES.map((t) => (
            <button
              key={t.id}
              onClick={() => setTf(t.id)}
              className={`px-3 py-1 rounded text-xs font-bold transition ${
                tf === t.id
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Ticker header ── */}
      {selected && (
        <div className="flex flex-wrap items-baseline gap-4">
          <span className="text-2xl font-black text-white">{selected}</span>
          {last != null && (
            <span className="text-xl font-mono text-gray-300">{fmtPrice(last)}</span>
          )}
          {chg != null && (
            <span className={`text-sm font-bold ${isUp ? 'text-green-400' : 'text-red-400'}`}>
              {isUp ? '+' : ''}{chg.toFixed(2)}% ({tf})
            </span>
          )}
        </div>
      )}

      {/* ── Stat row ── */}
      {selected && chartData.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {high52 != null && <Stat label={`${tf} High`}   value={high52.toFixed(2)}  color="text-green-400" />}
          {low52  != null && <Stat label={`${tf} Low`}    value={low52.toFixed(2)}   color="text-red-400" />}
          {avgVol != null && <Stat label="Avg Volume"      value={Number(avgVol).toLocaleString()} />}
          {stats.ticks   != null && <Stat label="Ticks in DB" value={stats.ticks.toLocaleString()} />}
        </div>
      )}

      {/* ── Chart area ── */}
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
        {loading && (
          <div className="flex items-center justify-center h-64 text-gray-600 text-sm animate-pulse">
            Loading…
          </div>
        )}

        {!loading && error && (
          <div className="flex items-center justify-center h-64 text-yellow-600 text-sm text-center px-8">
            {error}
          </div>
        )}

        {!loading && !error && chartData.length === 0 && (
          <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
            No data in this timeframe.
          </div>
        )}

        {!loading && !error && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={320}>
            <AreaChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: 8 }}>
              <defs>
                <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={stroke} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={stroke} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis
                dataKey="scraped_at"
                tickFormatter={(v) => fmtTimestamp(v, tf)}
                tick={{ fill: '#6b7280', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={60}
              />
              <YAxis
                tickFormatter={fmtAxis}
                tick={{ fill: '#6b7280', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={48}
                domain={['auto', 'auto']}
              />
              {first != null && (
                <ReferenceLine y={first} stroke="#374151" strokeDasharray="4 3" />
              )}
              <Tooltip
                content={<ChartTooltip tfId={tf} />}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="close"
                stroke={stroke}
                strokeWidth={2}
                fill={`url(#${gradId})`}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── Data tip ── */}
      <p className="text-[11px] text-gray-700">
        Live ticks accumulate every 15s during market hours.
        For 1W / 1M / 1Y, run <code className="bg-gray-800 px-1 rounded">python -m scripts.fetch_historical</code> to populate EOD history.
      </p>
    </div>
  )
}
