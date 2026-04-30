/**
 * usePortfolioStore — portfolio data + async fetch/refresh.
 *
 * valueHistory is seeded from DB snapshots on the first fetch so the equity
 * curve survives page refreshes. In-session fetches append new points on top.
 */

import { create } from 'zustand'
import { getPortfolio, getSnapshots } from '../api/portfolio'

const MAX_HISTORY = 500   // keep last 500 snapshots (DB-backed, so can be larger)

export const usePortfolioStore = create((set, get) => ({
  portfolio:      null,
  loading:        false,
  error:          null,
  lastFetch:      null,
  valueHistory:   [],     // [{ ts: ms, value: number }] — persisted equity snapshots
  _historySeeded: false,  // true after we've loaded from DB once per session

  fetch: async () => {
    if (get().loading) return
    set({ loading: true, error: null })
    try {
      // Seed from DB snapshots on the very first fetch (survives page refresh)
      let baseHistory = get().valueHistory
      if (!get()._historySeeded) {
        try {
          const { snapshots } = await getSnapshots(500)
          // snapshots are already sorted oldest-first from the API
          baseHistory = snapshots   // { ts: ms, value }
        } catch (_) {
          // Non-fatal: just start from empty if snapshots endpoint fails
          baseHistory = []
        }
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

  // Optimistic update helpers
  // Call before trade API; returns a rollback function
  optimisticTrade: (side, shares, price) => {
    const prev = get().portfolio
    if (!prev) return () => {}   // nothing to optimise

    // Compute tentative cash change
    const gross = shares * price
    const fee   = gross * 0.005
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
