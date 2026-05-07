/**
 * Charts — per-symbol price chart with 1D / 1W / 1M / 1Y timeframes.
 *
 * Symbol selection: searchable dropdown + recent symbols strip.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts'
import { api } from '../api/client'

// ── Timeframe config ──────────────────────────────────────────────────────────

const TIMEFRAMES = [
  { id: '1D', label: '1D', seconds: 24 * 3600 },
  { id: '1W', label: '1W', seconds: 7 * 24 * 3600 },
  { id: '1M', label: '1M', seconds: 30 * 24 * 3600 },
  { id: '1Y', label: '1Y', seconds: 365 * 24 * 3600 },
]

const MAX_RECENT = 8

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPrice(v) {
  return `PKR ${Number(v).toLocaleString('en-PK', { maximumFractionDigits: 2 })}`
}

function fmtAxis(v) {
  if (v >= 1000) return `${(v / 1000).toFixed(1)}K`
  return v.toFixed(0)
}

/**
 * Smart formatter: if the data spans less than 24 hours (all intraday),
 * always show HH:MM regardless of the selected timeframe.
 * Only show dates when the data actually spans multiple days.
 */
function fmtTimestamp(ts, tfId, spanSeconds) {
  const d = new Date(ts * 1000)
  const isIntraday = spanSeconds != null && spanSeconds < 24 * 3600
  if (tfId === '1D' || isIntraday) {
    return d.toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' })
  }
  if (tfId === '1W') return d.toLocaleDateString('en-PK', { weekday: 'short', day: 'numeric' })
  return d.toLocaleDateString('en-PK', { day: 'numeric', month: 'short' })
}

function pctChange(first, last) {
  if (!first || first === 0) return null
  return ((last - first) / first * 100)
}

// ── Symbol search dropdown ────────────────────────────────────────────────────

function SymbolSearch({ symbols, selected, onSelect }) {
  const [query,    setQuery]    = useState('')
  const [open,     setOpen]     = useState(false)
  const [focused,  setFocused]  = useState(0)
  const inputRef = useRef(null)
  const listRef  = useRef(null)

  const filtered = query.trim().length === 0
    ? symbols
    : symbols.filter((s) => s.includes(query.trim().toUpperCase()))

  const choose = (sym) => {
    onSelect(sym)
    setQuery('')
    setOpen(false)
    setFocused(0)
  }

  const handleKey = (e) => {
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocused((f) => Math.min(f + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocused((f) => Math.max(f - 1, 0))
    } else if (e.key === 'Enter' && filtered[focused]) {
      choose(filtered[focused])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  // Scroll focused item into view
  useEffect(() => {
    const el = listRef.current?.children[focused]
    el?.scrollIntoView({ block: 'nearest' })
  }, [focused])

  return (
    <div className="relative">
      <div className={`flex items-center gap-2 bg-gray-900 border rounded-xl px-3 py-2 transition ${open ? 'border-blue-500' : 'border-gray-700'}`}>
        <svg className="w-4 h-4 text-gray-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
        </svg>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); setFocused(0) }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={handleKey}
          placeholder={selected || 'Search symbol…'}
          className="bg-transparent text-sm text-white placeholder-gray-500 outline-none w-36"
        />
        {selected && !query && (
          <span className="text-xs font-bold text-blue-400 shrink-0">{selected}</span>
        )}
        <svg className={`w-3.5 h-3.5 text-gray-600 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {open && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-56 bg-gray-900 border border-gray-700 rounded-xl shadow-2xl overflow-hidden">
          <div
            ref={listRef}
            className="max-h-64 overflow-y-auto"
          >
            {filtered.map((sym, i) => (
              <button
                key={sym}
                onMouseDown={() => choose(sym)}
                className={`w-full text-left px-3 py-1.5 text-sm font-mono transition ${
                  i === focused
                    ? 'bg-blue-600 text-white'
                    : sym === selected
                    ? 'bg-gray-800 text-blue-400'
                    : 'text-gray-300 hover:bg-gray-800'
                }`}
              >
                {sym}
              </button>
            ))}
          </div>
          <div className="px-3 py-1.5 border-t border-gray-800 text-[10px] text-gray-600">
            {filtered.length} of {symbols.length} symbols
          </div>
        </div>
      )}
    </div>
  )
}

// ── Recent symbols strip ──────────────────────────────────────────────────────

function RecentStrip({ recent, selected, onSelect }) {
  if (recent.length === 0) return null
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider shrink-0">Recent</span>
      <div className="flex gap-1.5 flex-wrap">
        {recent.map((sym) => (
          <button
            key={sym}
            onClick={() => onSelect(sym)}
            className={`px-2.5 py-0.5 rounded-lg text-xs font-bold border transition ${
              sym === selected
                ? 'bg-blue-600 border-blue-500 text-white'
                : 'bg-gray-900 border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
            }`}
          >
            {sym}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Custom chart tooltip ──────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label, tfId, spanSecs }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">{fmtTimestamp(label, tfId, spanSecs)}</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        {d.open  != null && <><span className="text-gray-500">Open</span>  <span className="text-right font-mono">{d.open.toFixed(2)}</span></>}
        {d.high  != null && <><span className="text-gray-500">High</span>  <span className="text-right font-mono text-green-400">{d.high.toFixed(2)}</span></>}
        {d.low   != null && <><span className="text-gray-500">Low</span>   <span className="text-right font-mono text-red-400">{d.low.toFixed(2)}</span></>}
        <span className="text-gray-500">Close</span><span className="text-right font-mono text-white">{d.close?.toFixed(2)}</span>
        {d.volume != null && <><span className="text-gray-500">Vol</span>  <span className="text-right font-mono text-blue-300">{Number(d.volume).toLocaleString()}</span></>}
      </div>
    </div>
  )
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function Stat({ label, value, color }) {
  return (
    <div className="bg-gray-900 rounded-xl px-4 py-2.5 border border-gray-800">
      <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`text-sm font-mono font-bold ${color || 'text-white'}`}>{value}</div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Charts() {
  const [symbols,    setSymbols]    = useState([])
  const [statsMap,   setStatsMap]   = useState({})
  const [selected,   setSelected]   = useState(null)
  const [recent,     setRecent]     = useState([])   // recently viewed symbols
  const [tf,         setTf]         = useState('1D')
  const [chartData,  setChartData]  = useState([])
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState(null)
  const [reloadKey,  setReloadKey]  = useState(0)   // bump to force re-fetch same sym+tf

  // Load symbol list
  useEffect(() => {
    api('/api/history/stats')
      .then((res) => {
        const syms = Object.keys(res.data || {}).sort()
        setSymbols(syms)
        setStatsMap(res.data || {})
        if (syms.length > 0) setSelected(syms[0])
      })
      .catch((e) => setError(e.message))
  }, [])

  // Track recently viewed
  const handleSelect = useCallback((sym) => {
    setSelected(sym)
    setRecent((prev) => [sym, ...prev.filter((s) => s !== sym)].slice(0, MAX_RECENT))
  }, [])

  // Fetch chart data
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
      setChartData([])
      setError(
        e.status === 404
          ? `No data for ${sym} in this timeframe. Run fetch-historical or wait for live ticks.`
          : e.message,
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected) loadChart(selected, tf)
  }, [selected, tf, loadChart, reloadKey])

  // Chart-level stats
  const closes = chartData.map((d) => d.close).filter(Boolean)
  const highs  = chartData.map((d) => d.high).filter(Boolean)
  const lows   = chartData.map((d) => d.low).filter(Boolean)
  const first  = closes[0]
  const last   = closes[closes.length - 1]
  const chg    = pctChange(first, last)
  const isUp   = chg == null ? true : chg >= 0
  const stroke = isUp ? '#22c55e' : '#f97316'
  const gradId = isUp ? 'grad-up' : 'grad-dn'
  const avgVol = chartData.length
    ? Math.round(chartData.reduce((s, d) => s + (d.volume || 0), 0) / chartData.length)
    : null
  const stats = statsMap[selected] || {}

  // Detect actual data span so the X-axis can format correctly
  const tsArr    = chartData.map((d) => d.scraped_at).filter(Boolean)
  const spanSecs = tsArr.length >= 2 ? tsArr[tsArr.length - 1] - tsArr[0] : null
  const isIntradayData = spanSecs != null && spanSecs < 24 * 3600
  // Show a notice for multi-day timeframes when DB only has intraday data
  const limitedDataNotice = tf !== '1D' && isIntradayData && chartData.length > 0

  return (
    <div className="p-5 space-y-5 max-w-6xl mx-auto">

      {/* ── Top bar: search + timeframe ── */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <h2 className="text-base font-bold text-white">📉 Price Charts</h2>
          {symbols.length > 0 && (
            <span className="text-xs text-gray-600">{symbols.length} symbols</span>
          )}
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          {/* Search */}
          <SymbolSearch symbols={symbols} selected={selected} onSelect={handleSelect} />

          {/* Timeframe */}
          <div className="flex gap-1 bg-gray-900 border border-gray-700 rounded-xl p-0.5">
            {TIMEFRAMES.map((t) => (
              <button
                key={t.id}
                onClick={() => setTf(t.id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition ${
                  tf === t.id
                    ? 'bg-blue-600 text-white shadow'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Reload — re-fetches current symbol+tf from DB (useful after fetch_historical) */}
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            disabled={loading}
            title="Reload chart data from DB"
            className="px-2.5 py-1.5 rounded-xl border border-gray-700 bg-gray-900 text-gray-500
                       hover:text-gray-300 hover:border-gray-600 transition text-xs disabled:opacity-40"
          >
            {loading ? '…' : '↺'}
          </button>
        </div>
      </div>

      {/* ── Recent strip ── */}
      <RecentStrip recent={recent} selected={selected} onSelect={handleSelect} />

      {/* ── Ticker headline ── */}
      {selected && (
        <div className="flex flex-wrap items-baseline gap-4">
          <span className="text-3xl font-black text-white">{selected}</span>
          {last != null && (
            <span className="text-xl font-mono text-gray-300">{fmtPrice(last)}</span>
          )}
          {chg != null && (
            <span className={`text-sm font-bold px-2 py-0.5 rounded-lg ${
              isUp ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
            }`}>
              {isUp ? '+' : ''}{chg.toFixed(2)}% ({tf})
            </span>
          )}
        </div>
      )}

      {/* ── Stat cards ── */}
      {selected && chartData.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {highs.length > 0 && <Stat label={`${tf} High`}    value={Math.max(...highs).toFixed(2)} color="text-green-400" />}
          {lows.length  > 0 && <Stat label={`${tf} Low`}     value={Math.min(...lows).toFixed(2)}  color="text-red-400" />}
          {avgVol       != null && <Stat label="Avg Volume"   value={Number(avgVol).toLocaleString()} />}
          {stats.ticks  != null && <Stat label="Ticks in DB"  value={stats.ticks.toLocaleString()} />}
        </div>
      )}

      {/* ── Chart ── */}
      <div className="bg-gray-900 rounded-2xl border border-gray-800 p-5">
        {loading && (
          <div className="flex items-center justify-center h-72 text-gray-600 text-sm animate-pulse">
            Loading {selected}…
          </div>
        )}
        {!loading && error && (
          <div className="flex items-center justify-center h-72 text-yellow-600 text-sm text-center px-8">
            {error}
          </div>
        )}
        {!loading && !error && chartData.length === 0 && (
          <div className="flex items-center justify-center h-72 text-gray-600 text-sm">
            No data in this timeframe.
          </div>
        )}
        {!loading && !error && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={340}>
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
                tickFormatter={(v) => fmtTimestamp(v, tf, spanSecs)}
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
              <Tooltip content={<ChartTooltip tfId={tf} spanSecs={spanSecs} />} isAnimationActive={false} />
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

      {limitedDataNotice && (
        <div className="flex items-center gap-2 px-3 py-2 bg-yellow-950/40 border border-yellow-800/40 rounded-lg text-[11px] text-yellow-600">
          <span>⚠</span>
          <span>
            Only today's data is available for <strong>{tf}</strong> view — showing intraday ticks with time labels.
            Run <code className="bg-gray-800 px-1 rounded">python -m scripts.fetch_historical</code> to populate historical data.
          </span>
        </div>
      )}

      <p className="text-[11px] text-gray-700">
        Live ticks every 15s during market hours. For 1W / 1M / 1Y run{' '}
        <code className="bg-gray-800 px-1 rounded">python -m scripts.fetch_historical</code>.
      </p>
    </div>
  )
}
