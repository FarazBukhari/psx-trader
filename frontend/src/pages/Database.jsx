/**
 * Database — raw DB viewer.
 *
 * Three sub-tabs:
 *   • Overview      — symbol list + tick counts
 *   • Price History — OHLCV ticks for any symbol, paginated
 *   • Signals Log   — non-HOLD signal records for any symbol
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'

const MAX_RECENT = 8

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTs(unix) {
  if (!unix) return '—'
  return new Date(unix * 1000).toLocaleString('en-PK', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

function fmtNum(v, dec = 2) {
  if (v == null) return '—'
  return Number(v).toLocaleString('en-PK', { maximumFractionDigits: dec })
}

function pct(v) {
  if (v == null) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

// ── Shared: searchable symbol dropdown ────────────────────────────────────────

function SymbolSearch({ symbols, selected, onSelect }) {
  const [query,   setQuery]   = useState('')
  const [open,    setOpen]    = useState(false)
  const [focused, setFocused] = useState(0)
  const listRef = useRef(null)

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
    if (e.key === 'ArrowDown') { e.preventDefault(); setFocused((f) => Math.min(f + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setFocused((f) => Math.max(f - 1, 0)) }
    else if (e.key === 'Enter' && filtered[focused]) choose(filtered[focused])
    else if (e.key === 'Escape') setOpen(false)
  }

  useEffect(() => {
    listRef.current?.children[focused]?.scrollIntoView({ block: 'nearest' })
  }, [focused])

  return (
    <div className="relative">
      <div className={`flex items-center gap-2 bg-gray-900 border rounded-xl px-3 py-2 transition ${open ? 'border-blue-500' : 'border-gray-700'}`}>
        <svg className="w-4 h-4 text-gray-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
        </svg>
        <input
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
          <div ref={listRef} className="max-h-64 overflow-y-auto">
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

function RecentStrip({ recent, selected, onSelect }) {
  if (recent.length === 0) return null
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-[10px] text-gray-600 uppercase tracking-wider shrink-0">Recent</span>
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
  )
}

// ── Shared: table primitives ───────────────────────────────────────────────────

function TableWrap({ children }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-gray-800">
      <table className="w-full text-xs text-left">{children}</table>
    </div>
  )
}

function Th({ children, right }) {
  return (
    <th className={`px-3 py-2 bg-gray-900 text-gray-500 font-semibold uppercase tracking-wider whitespace-nowrap ${right ? 'text-right' : ''}`}>
      {children}
    </th>
  )
}

function Td({ children, right, mono, color }) {
  return (
    <td className={`px-3 py-1.5 border-t border-gray-800/60 whitespace-nowrap ${right ? 'text-right' : ''} ${mono ? 'font-mono' : ''} ${color || 'text-gray-300'}`}>
      {children}
    </td>
  )
}

function StatusRow({ cols, msg }) {
  return (
    <tr>
      <td colSpan={cols} className="px-3 py-8 text-center text-gray-600 text-xs">{msg}</td>
    </tr>
  )
}

function PageControls({ page, totalPages, onPrev, onNext }) {
  return (
    <div className="flex items-center justify-between px-1 text-xs text-gray-500">
      <span>Page {page} of {totalPages || 1}</span>
      <div className="flex gap-2">
        <button onClick={onPrev} disabled={page <= 1}
          className="px-3 py-1 rounded border border-gray-700 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed">
          ← Prev
        </button>
        <button onClick={onNext} disabled={page >= totalPages}
          className="px-3 py-1 rounded border border-gray-700 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed">
          Next →
        </button>
      </div>
    </div>
  )
}

// ── Price History tab ─────────────────────────────────────────────────────────

const PRICE_PAGE = 50

function PriceHistoryTab({ symbols, statsMap }) {
  const [selected, setSelected] = useState(symbols[0] || null)
  const [recent,   setRecent]   = useState([])
  const [allRows,  setAllRows]  = useState([])
  const [rows,     setRows]     = useState([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [page,     setPage]     = useState(1)

  const handleSelect = useCallback((sym) => {
    setSelected(sym)
    setRecent((prev) => [sym, ...prev.filter((s) => s !== sym)].slice(0, MAX_RECENT))
  }, [])

  const load = useCallback(async (sym) => {
    if (!sym) return
    setLoading(true)
    setError(null)
    try {
      const res  = await api(`/api/history/${sym}?n=2000`)
      const data = [...(res.data || [])].reverse()   // newest first
      setAllRows(data)
      setPage(1)
    } catch (e) {
      setError(e.status === 404 ? 'No price history for this symbol yet.' : e.message)
      setAllRows([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (selected) load(selected) }, [selected, load])

  const totalPages = Math.max(1, Math.ceil(allRows.length / PRICE_PAGE))
  useEffect(() => {
    const start = (page - 1) * PRICE_PAGE
    setRows(allRows.slice(start, start + PRICE_PAGE))
  }, [allRows, page])

  const stats = statsMap[selected] || {}

  return (
    <div className="space-y-4">
      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-4 justify-between">
        <div className="flex items-center gap-3 flex-wrap">
          <SymbolSearch symbols={symbols} selected={selected} onSelect={handleSelect} />
          <RecentStrip recent={recent} selected={selected} onSelect={handleSelect} />
        </div>
        {selected && stats.ticks != null && (
          <div className="text-[11px] text-gray-600 text-right leading-5 shrink-0">
            <div><span className="text-gray-400 font-mono">{stats.ticks.toLocaleString()}</span> ticks total</div>
            <div>{fmtTs(stats.first_at)} → {fmtTs(stats.last_at)}</div>
          </div>
        )}
      </div>

      <TableWrap>
        <thead>
          <tr>
            <Th>Timestamp</Th>
            <Th right>Open</Th>
            <Th right>High</Th>
            <Th right>Low</Th>
            <Th right>Close</Th>
            <Th right>LDCP</Th>
            <Th right>Chg %</Th>
            <Th right>Volume</Th>
            <Th>Sector</Th>
            <Th>Source</Th>
          </tr>
        </thead>
        <tbody>
          {loading && <StatusRow cols={10} msg="Loading…" />}
          {!loading && error   && <StatusRow cols={10} msg={error} />}
          {!loading && !error && rows.length === 0 && <StatusRow cols={10} msg="No data" />}
          {!loading && !error && rows.map((r) => {
            const chgColor = r.change_pct == null ? '' : r.change_pct >= 0 ? 'text-green-400' : 'text-red-400'
            return (
              <tr key={r.id} className="hover:bg-gray-800/30 transition-colors">
                <Td mono>{fmtTs(r.scraped_at)}</Td>
                <Td right mono>{fmtNum(r.open)}</Td>
                <Td right mono color="text-green-400">{fmtNum(r.high)}</Td>
                <Td right mono color="text-red-400">{fmtNum(r.low)}</Td>
                <Td right mono color="text-white">{fmtNum(r.close)}</Td>
                <Td right mono>{fmtNum(r.ldcp)}</Td>
                <Td right mono color={chgColor}>{pct(r.change_pct)}</Td>
                <Td right mono>{r.volume != null ? Number(r.volume).toLocaleString() : '—'}</Td>
                <Td>{r.sector || '—'}</Td>
                <Td>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                    r.source === 'live'       ? 'bg-green-900/50 text-green-400'
                    : r.source === 'historical' ? 'bg-blue-900/50 text-blue-400'
                    : 'bg-gray-800 text-gray-500'
                  }`}>
                    {r.source || '—'}
                  </span>
                </Td>
              </tr>
            )
          })}
        </tbody>
      </TableWrap>

      {allRows.length > PRICE_PAGE && (
        <PageControls page={page} totalPages={totalPages}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(totalPages, p + 1))} />
      )}

      <p className="text-[11px] text-gray-700">
        Showing newest {Math.min(2000, allRows.length).toLocaleString()} of {stats.ticks?.toLocaleString() || '?'} ticks in DB.
      </p>
    </div>
  )
}

// ── Signals Log tab ───────────────────────────────────────────────────────────

const SIG_PAGE = 50

const SIG_COLOR = {
  BUY:        'bg-green-900/50 text-green-400',
  SELL:       'bg-red-900/50 text-red-400',
  FORCE_SELL: 'bg-orange-900/50 text-orange-400',
  HOLD:       'bg-gray-800 text-gray-500',
}

function SignalsTab({ symbols }) {
  const [selected, setSelected] = useState(symbols[0] || null)
  const [recent,   setRecent]   = useState([])
  const [allRows,  setAllRows]  = useState([])
  const [rows,     setRows]     = useState([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [page,     setPage]     = useState(1)

  const handleSelect = useCallback((sym) => {
    setSelected(sym)
    setRecent((prev) => [sym, ...prev.filter((s) => s !== sym)].slice(0, MAX_RECENT))
  }, [])

  const load = useCallback(async (sym) => {
    if (!sym) return
    setLoading(true)
    setError(null)
    try {
      const res = await api(`/api/history/${sym}/signals?limit=500`)
      setAllRows(res.data || [])
      setPage(1)
    } catch (e) {
      setError(e.status === 404 ? 'No signals recorded for this symbol yet.' : e.message)
      setAllRows([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (selected) load(selected) }, [selected, load])

  const totalPages = Math.max(1, Math.ceil(allRows.length / SIG_PAGE))
  useEffect(() => {
    const start = (page - 1) * SIG_PAGE
    setRows(allRows.slice(start, start + SIG_PAGE))
  }, [allRows, page])

  return (
    <div className="space-y-4">
      {/* Controls row */}
      <div className="flex flex-wrap items-center gap-3">
        <SymbolSearch symbols={symbols} selected={selected} onSelect={handleSelect} />
        <RecentStrip recent={recent} selected={selected} onSelect={handleSelect} />
      </div>

      <TableWrap>
        <thead>
          <tr>
            <Th>Time</Th>
            <Th>Signal</Th>
            <Th>Prev</Th>
            <Th>Changed</Th>
            <Th right>RSI</Th>
            <Th right>SMA5</Th>
            <Th right>SMA20</Th>
            <Th right>Price</Th>
            <Th right>Vol</Th>
            <Th right>Score</Th>
            <Th right>Conf</Th>
            <Th>Sources</Th>
          </tr>
        </thead>
        <tbody>
          {loading && <StatusRow cols={12} msg="Loading…" />}
          {!loading && error   && <StatusRow cols={12} msg={error} />}
          {!loading && !error && rows.length === 0 && <StatusRow cols={12} msg="No signals recorded" />}
          {!loading && !error && rows.map((r) => (
            <tr key={r.id} className="hover:bg-gray-800/30 transition-colors">
              <Td mono>{fmtTs(r.generated_at)}</Td>
              <Td>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${SIG_COLOR[r.signal] || SIG_COLOR.HOLD}`}>
                  {r.signal}
                </span>
              </Td>
              <Td>
                {r.prev_signal && (
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${SIG_COLOR[r.prev_signal] || SIG_COLOR.HOLD}`}>
                    {r.prev_signal}
                  </span>
                )}
              </Td>
              <Td>
                {r.signal_changed
                  ? <span className="text-yellow-400 text-[10px] font-bold">YES</span>
                  : <span className="text-gray-700 text-[10px]">—</span>}
              </Td>
              <Td right mono>{fmtNum(r.rsi, 1)}</Td>
              <Td right mono>{fmtNum(r.sma5)}</Td>
              <Td right mono>{fmtNum(r.sma20)}</Td>
              <Td right mono>{fmtNum(r.price)}</Td>
              <Td right mono>{r.volume != null ? Number(r.volume).toLocaleString() : '—'}</Td>
              <Td right mono>{fmtNum(r.action_score, 0)}</Td>
              <Td right mono>
                {r.confidence != null
                  ? <span className={r.confidence > 0.5 ? 'text-green-400' : ''}>{(r.confidence * 100).toFixed(0)}%</span>
                  : '—'}
              </Td>
              <Td>
                <span className="text-gray-600">
                  {Array.isArray(r.signal_sources) && r.signal_sources.length > 0
                    ? r.signal_sources.join(', ')
                    : '—'}
                </span>
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>

      {allRows.length > SIG_PAGE && (
        <PageControls page={page} totalPages={totalPages}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(totalPages, p + 1))} />
      )}
    </div>
  )
}

// ── Overview tab ──────────────────────────────────────────────────────────────

function StatsTab({ symbols, statsMap }) {
  const total = Object.values(statsMap).reduce((s, v) => s + (v.ticks || 0), 0)
  return (
    <div className="space-y-4">
      <div className="flex gap-4 flex-wrap">
        <div className="bg-gray-900 rounded-xl px-4 py-2.5 border border-gray-800">
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">Symbols</div>
          <div className="text-lg font-bold text-white">{symbols.length}</div>
        </div>
        <div className="bg-gray-900 rounded-xl px-4 py-2.5 border border-gray-800">
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">Total ticks</div>
          <div className="text-lg font-bold text-white">{total.toLocaleString()}</div>
        </div>
      </div>
      <TableWrap>
        <thead>
          <tr>
            <Th>Symbol</Th>
            <Th right>Ticks</Th>
            <Th>First tick</Th>
            <Th>Latest tick</Th>
          </tr>
        </thead>
        <tbody>
          {symbols.length === 0 && <StatusRow cols={4} msg="No data in DB yet." />}
          {symbols.map((sym) => {
            const s = statsMap[sym] || {}
            return (
              <tr key={sym} className="hover:bg-gray-800/30 transition-colors">
                <Td><span className="font-bold text-white">{sym}</span></Td>
                <Td right mono>{(s.ticks || 0).toLocaleString()}</Td>
                <Td mono>{fmtTs(s.first_at)}</Td>
                <Td mono>{fmtTs(s.last_at)}</Td>
              </tr>
            )
          })}
        </tbody>
      </TableWrap>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

const DB_TABS = [
  { id: 'stats',   label: '📊 Overview' },
  { id: 'prices',  label: '💹 Price History' },
  { id: 'signals', label: '📡 Signals Log' },
]

export default function Database() {
  const [activeTab, setActiveTab] = useState('stats')
  const [symbols,   setSymbols]   = useState([])
  const [statsMap,  setStatsMap]  = useState({})
  const [loadErr,   setLoadErr]   = useState(null)

  useEffect(() => {
    api('/api/history/stats')
      .then((res) => {
        const map  = res.data || {}
        setSymbols(Object.keys(map).sort())
        setStatsMap(map)
      })
      .catch((e) => setLoadErr(e.message))
  }, [])

  const totalTicks = Object.values(statsMap).reduce((s, v) => s + (v.ticks || 0), 0)

  return (
    <div className="p-5 space-y-5 max-w-6xl mx-auto">
      <div className="flex items-center gap-3">
        <h2 className="text-base font-bold text-white">🗄️ Database Viewer</h2>
        {symbols.length > 0 && (
          <span className="text-xs text-gray-600">
            {symbols.length} symbols · {totalTicks.toLocaleString()} ticks
          </span>
        )}
      </div>

      {loadErr && (
        <div className="text-sm text-red-400 bg-red-900/20 border border-red-800 rounded-xl px-4 py-2">
          Failed to load stats: {loadErr}
        </div>
      )}

      {/* Sub-tab bar */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-0.5 w-fit">
        {DB_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`px-4 py-1.5 rounded-lg text-xs font-semibold transition ${
              activeTab === t.id ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === 'stats'   && <StatsTab       symbols={symbols} statsMap={statsMap} />}
      {activeTab === 'prices'  && symbols.length > 0 && <PriceHistoryTab symbols={symbols} statsMap={statsMap} />}
      {activeTab === 'signals' && symbols.length > 0 && <SignalsTab      symbols={symbols} />}
      {(activeTab === 'prices' || activeTab === 'signals') && symbols.length === 0 && (
        <div className="text-sm text-gray-600 py-8 text-center">
          No symbols in DB yet. Start the server and wait for the first poll cycle.
        </div>
      )}
    </div>
  )
}
