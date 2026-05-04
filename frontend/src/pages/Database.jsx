/**
 * Database — raw DB viewer.
 *
 * Two sub-tabs:
 *   • Price History  — OHLCV ticks for any symbol, paginated
 *   • Signals Log    — non-HOLD signal records for any symbol
 *
 * Data sources:
 *   GET /api/history/stats          — symbol list + tick counts
 *   GET /api/history/{symbol}?n=    — price ticks (oldest → newest)
 *   GET /api/history/{symbol}/signals?limit= — signal log
 */

import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'

// ── Helpers ──────────────────────────────────────────────────────────────────

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

// ── Shared sub-components ─────────────────────────────────────────────────────

function SymbolPicker({ symbols, selected, onSelect }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {symbols.map((sym) => (
        <button
          key={sym}
          onClick={() => onSelect(sym)}
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
  )
}

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
        <button
          onClick={onPrev}
          disabled={page <= 1}
          className="px-3 py-1 rounded border border-gray-700 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed"
        >
          ← Prev
        </button>
        <button
          onClick={onNext}
          disabled={page >= totalPages}
          className="px-3 py-1 rounded border border-gray-700 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed"
        >
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
  const [rows,     setRows]     = useState([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [page,     setPage]     = useState(1)
  const [total,    setTotal]    = useState(0)

  // Fetch up to 2000 ticks, keep all; paginate in JS for speed
  const [allRows, setAllRows] = useState([])

  const load = useCallback(async (sym) => {
    if (!sym) return
    setLoading(true)
    setError(null)
    try {
      const res = await api(`/api/history/${sym}?n=2000`)
      // Reverse so newest first
      const data = [...(res.data || [])].reverse()
      setAllRows(data)
      setTotal(data.length)
      setPage(1)
    } catch (e) {
      setError(e.status === 404 ? 'No price history for this symbol yet.' : e.message)
      setAllRows([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected) load(selected)
  }, [selected, load])

  // Client-side pagination
  const totalPages = Math.max(1, Math.ceil(allRows.length / PRICE_PAGE))
  useEffect(() => {
    const start = (page - 1) * PRICE_PAGE
    setRows(allRows.slice(start, start + PRICE_PAGE))
  }, [allRows, page])

  const stats = statsMap[selected] || {}

  return (
    <div className="space-y-4">
      {/* Symbol picker + stats */}
      <div className="flex flex-wrap items-start gap-4">
        <div className="flex-1 min-w-0">
          <SymbolPicker symbols={symbols} selected={selected} onSelect={(s) => setSelected(s)} />
        </div>
        {selected && stats.ticks != null && (
          <div className="text-[11px] text-gray-600 shrink-0 text-right leading-5">
            <div><span className="text-gray-400">{stats.ticks.toLocaleString()}</span> ticks total</div>
            <div>From {fmtTs(stats.first_at)} → {fmtTs(stats.last_at)}</div>
          </div>
        )}
      </div>

      {/* Table */}
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
          {!loading && error && <StatusRow cols={10} msg={error} />}
          {!loading && !error && rows.length === 0 && <StatusRow cols={10} msg="No data" />}
          {!loading && !error && rows.map((r) => {
            const chg = r.change_pct
            const chgColor = chg == null ? '' : chg >= 0 ? 'text-green-400' : 'text-red-400'
            return (
              <tr key={r.id} className="hover:bg-gray-800/30 transition-colors">
                <Td mono>{fmtTs(r.scraped_at)}</Td>
                <Td right mono>{fmtNum(r.open)}</Td>
                <Td right mono color="text-green-400">{fmtNum(r.high)}</Td>
                <Td right mono color="text-red-400">{fmtNum(r.low)}</Td>
                <Td right mono color="text-white">{fmtNum(r.close)}</Td>
                <Td right mono>{fmtNum(r.ldcp)}</Td>
                <Td right mono color={chgColor}>{pct(chg)}</Td>
                <Td right mono>{r.volume != null ? Number(r.volume).toLocaleString() : '—'}</Td>
                <Td>{r.sector || '—'}</Td>
                <Td>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                    r.source === 'live' ? 'bg-green-900/50 text-green-400'
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
        <PageControls
          page={page}
          totalPages={totalPages}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(totalPages, p + 1))}
        />
      )}

      <p className="text-[11px] text-gray-700">
        Showing newest {Math.min(2000, total).toLocaleString()} of {stats.ticks?.toLocaleString() || '?'} ticks in DB.
        Use <code className="bg-gray-800 px-1 rounded">fetch_historical</code> to load EOD history.
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
  const [allRows,  setAllRows]  = useState([])
  const [rows,     setRows]     = useState([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [page,     setPage]     = useState(1)

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

  useEffect(() => {
    if (selected) load(selected)
  }, [selected, load])

  const totalPages = Math.max(1, Math.ceil(allRows.length / SIG_PAGE))
  useEffect(() => {
    const start = (page - 1) * SIG_PAGE
    setRows(allRows.slice(start, start + SIG_PAGE))
  }, [allRows, page])

  return (
    <div className="space-y-4">
      <SymbolPicker symbols={symbols} selected={selected} onSelect={(s) => setSelected(s)} />

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
          {!loading && error && <StatusRow cols={12} msg={error} />}
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
        <PageControls
          page={page}
          totalPages={totalPages}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(totalPages, p + 1))}
        />
      )}
    </div>
  )
}

// ── Stats overview tab ────────────────────────────────────────────────────────

function StatsTab({ symbols, statsMap }) {
  const total = Object.values(statsMap).reduce((s, v) => s + (v.ticks || 0), 0)

  return (
    <div className="space-y-4">
      <div className="flex gap-4 flex-wrap">
        <div className="bg-gray-900 rounded-lg px-4 py-2.5 border border-gray-800">
          <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-0.5">Symbols tracked</div>
          <div className="text-lg font-bold text-white">{symbols.length}</div>
        </div>
        <div className="bg-gray-900 rounded-lg px-4 py-2.5 border border-gray-800">
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
        const syms = Object.keys(map).sort()
        setSymbols(syms)
        setStatsMap(map)
      })
      .catch((e) => setLoadErr(e.message))
  }, [])

  return (
    <div className="p-5 space-y-5">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <h2 className="text-base font-bold text-white">🗄️ Database Viewer</h2>
        {symbols.length > 0 && (
          <span className="text-xs text-gray-600">{symbols.length} symbols · {Object.values(statsMap).reduce((s, v) => s + (v.ticks || 0), 0).toLocaleString()} total ticks</span>
        )}
      </div>

      {loadErr && (
        <div className="text-sm text-red-400 bg-red-900/20 border border-red-800 rounded-lg px-4 py-2">
          Failed to load stats: {loadErr}
        </div>
      )}

      {/* Sub-tab bar */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-0.5 w-fit">
        {DB_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`px-4 py-1.5 rounded text-xs font-semibold transition ${
              activeTab === t.id
                ? 'bg-gray-700 text-white'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'stats'   && <StatsTab    symbols={symbols} statsMap={statsMap} />}
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
