/**
 * usePortfolioStore — portfolio data + async fetch/refresh.
 */

import { create } from 'zustand'
import { getPortfolio } from '../api/portfolio'

const MAX_HISTORY = 120   // keep last 120 snapshots

export const usePortfolioStore = create((set, get) => ({
  portfolio:    null,
  loading:      false,
  error:        null,
  lastFetch:    null,
  valueHistory: [],   // [{ ts: number, value: number }] — in-session equity snapshots

  fetch: async () => {
    if (get().loading) return
    set({ loading: true, error: null })
    try {
      const data = await getPortfolio()
      const now  = Date.now()
      const snap = { ts: now, value: data.total_portfolio_value }
      const prev = get().valueHistory
      const next = [...prev, snap].slice(-MAX_HISTORY)
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
