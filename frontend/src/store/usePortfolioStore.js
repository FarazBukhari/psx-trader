/**
 * usePortfolioStore — portfolio data + async fetch/refresh.
 *
 * valueHistory is seeded from DB snapshots on the first fetch so the equity
 * curve survives page refreshes. In-session fetches append new points on top.
 */

import { create } from 'zustand'
import { getPortfolio, getSnapshots, getPortfolioHistoryToday } from '../api/portfolio'

const MAX_HISTORY   = 5000      // one tick every 5s × 6h session = 4320 max; buffer to 5000
const STORAGE_KEY   = 'psx_portfolio_history'
const SAVE_INTERVAL = 10_000    // write localStorage at most once per 10 s

/** ms timestamp for 09:30 local time today (assumes browser clock in PKT). */
export function todaySessionStartMs() {
  const d = new Date()
  d.setHours(9, 30, 0, 0)
  return d.getTime()
}

/** ms timestamp for 15:30 local time today — PSX session close. */
export function todaySessionEndMs() {
  const d = new Date()
  d.setHours(15, 30, 0, 0)
  return d.getTime()
}

// ── localStorage persistence ───────────────────────────────────────────────────

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const { date, history } = JSON.parse(raw)
    if (date !== new Date().toDateString()) return []   // yesterday's data — discard
    const sessionStart = todaySessionStartMs()
    return history.filter((h) => h.ts >= sessionStart)
  } catch {
    return []
  }
}

let _lastStorageSave = 0

function saveToStorage(history) {
  const now = Date.now()
  if (now - _lastStorageSave < SAVE_INTERVAL) return
  _lastStorageSave = now
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      date:    new Date().toDateString(),
      history: history.slice(-MAX_HISTORY),
    }))
  } catch { /* storage full — non-fatal */ }
}

export const usePortfolioStore = create((set, get) => ({
  portfolio:      null,
  loading:        false,
  error:          null,
  lastFetch:      null,
  valueHistory:   loadFromStorage(),  // rehydrate from localStorage on init

  _historySeeded: false,  // true after we've loaded from DB once per session

  fetch: async () => {
    if (get().loading) return
    set({ loading: true, error: null })
    try {
      // Seed history on the very first fetch.
      //
      // Sources (merged in priority order, oldest-first):
      //   1. Intraday reconstruction — tick-resolution curve rebuilt from
      //      today's live price_history ticks for each position. Covers 09:30
      //      to now even if the page was never open before.
      //   2. localStorage — survives page refreshes; may have denser recent
      //      data than the DB reconstruction if the page was already open.
      //   3. DB snapshots — 5-minute resolution fallback if both above fail.
      let baseHistory = get().valueHistory   // populated from localStorage on init
      if (!get()._historySeeded) {
        try {
          const sessionStart = todaySessionStartMs()

          // Fetch intraday reconstruction in parallel with optional snapshot fallback
          const [histRes] = await Promise.allSettled([
            getPortfolioHistoryToday(),
          ])

          const intradayPoints = histRes.status === 'fulfilled'
            ? (histRes.value.points ?? []).filter((p) => p.ts >= sessionStart)
            : []

          if (intradayPoints.length > 0) {
            // Merge: intraday covers 09:30→now; localStorage fills recent gaps.
            // Keep intraday as the base, then append any localStorage points
            // that are newer than the last intraday point.
            const intradayCutoff = intradayPoints[intradayPoints.length - 1].ts
            const recentLocal    = baseHistory.filter((p) => p.ts > intradayCutoff)
            baseHistory = [...intradayPoints, ...recentLocal]
          } else if (baseHistory.length === 0) {
            // Neither intraday nor localStorage — fall back to 5-min DB snapshots
            try {
              const { snapshots } = await getSnapshots(500)
              baseHistory = snapshots.filter((s) => s.ts >= sessionStart)
            } catch (_) { /* non-fatal */ }
          }
        } catch (_) { /* non-fatal — keep whatever localStorage gave us */ }

        set({ _historySeeded: true })
      }

      const data = await getPortfolio()
      const now  = Date.now()

      // Only append a new in-memory point if the value differs from the last DB snap
      // (avoids duplicate at the seam on first load)
      const last  = baseHistory[baseHistory.length - 1]
      const snap  = { ts: now, value: data.total_portfolio_value }
      const next  = (last && Math.abs(last.value - snap.value) < 0.01 && now - last.ts < 60_000)
        ? baseHistory
        : [...baseHistory, snap].slice(-MAX_HISTORY)

      set({ portfolio: data, loading: false, lastFetch: now, valueHistory: next })
    } catch (err) {
      set({ error: err.message || 'Failed to load portfolio', loading: false })
    }
  },

  setPortfolio: (portfolio) => {
    // Also append to value history when portfolio is set directly (e.g. after trade)
    if (portfolio?.total_portfolio_value != null) {
      const snap = { ts: Date.now(), value: portfolio.total_portfolio_value }
      const prev = get().valueHistory
      const next = [...prev, snap].slice(-MAX_HISTORY)
      set({ portfolio, valueHistory: next })
    } else {
      set({ portfolio })
    }
  },
  clearError: () => set({ error: null }),

  /**
   * updateLiveValue — called on every WebSocket tick.
   * Recomputes total portfolio value from live prices and updates the
   * equity curve history (rate-limited to 1 point per 15 s to avoid bloat).
   *
   * @param {Record<string, number>} livePrice  symbol → current price map
   */
  updateLiveValue: (livePrice) => {
    const { portfolio, valueHistory } = get()
    if (!portfolio) return

    const positions = portfolio.positions || []
    const investedValue = positions.reduce((sum, pos) => {
      const price = livePrice[pos.symbol] ?? pos.current_price ?? pos.avg_buy_price
      return sum + price * pos.shares
    }, 0)
    const unrealizedPL = positions.reduce((sum, pos) => {
      const price = livePrice[pos.symbol] ?? pos.current_price ?? pos.avg_buy_price
      return sum + (price - pos.avg_buy_price) * pos.shares
    }, 0)
    const totalValue = portfolio.cash_available + investedValue

    // Append a chart point every 15 seconds so the curve updates regularly
    // without flooding history with per-tick noise.
    const now  = Date.now()
    if (now > todaySessionEndMs()) return   // market closed — freeze chart

    const last = valueHistory[valueHistory.length - 1]
    const shouldAppend = !last
      || Math.abs(last.value - totalValue) >= 0.5
      || now - last.ts >= 15_000

    const next = shouldAppend
      ? [...valueHistory, { ts: now, value: totalValue }].slice(-MAX_HISTORY)
      : valueHistory

    set({
      portfolio: {
        ...portfolio,
        total_portfolio_value: totalValue,
        invested_value:        investedValue,
        unrealized_pl:         unrealizedPL,
      },
      valueHistory: next,
    })

    // Persist to localStorage so history survives page refreshes (rate-limited internally)
    saveToStorage(next)
  },

  // Optimistic update helpers
  // Call before trade API; returns a rollback function
  optimisticTrade: (side, shares, price) => {
    const prev = get().portfolio
    if (!prev) return () => {}   // nothing to optimise

    // Compute tentative cash change
    // Finqalab: 0.25% brokerage only (confirmed from cashbook)
    const gross = shares * price
    const fee   = gross * 0.0025
    const delta = side === 'buy' ? -(gross + fee) : (gross - fee)

    set({
      portfolio: {
        ...prev,
        cash_available: Math.max(0, prev.cash_available + delta),
      },
    })

    // Return rollback function
    return () => set({ portfolio: prev })
  },
}))
