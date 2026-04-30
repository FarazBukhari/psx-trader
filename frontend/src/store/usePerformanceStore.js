/**
 * usePerformanceStore — forward-testing performance data + async fetch actions.
 *
 * State:
 *   liveTrades  — currently OPEN forward trades
 *   history     — CLOSED trades (paginated, newest-first)
 *   historyTotal— total CLOSED count for pagination
 *   summary     — aggregate stats (win_rate, expectancy, MFE/MAE, etc.)
 *   loading     — true while any fetch is in-flight
 *   error       — last error message, or null
 *
 * Actions:
 *   fetchSummary()       — refresh aggregate stats
 *   fetchLive()          — refresh open trades
 *   fetchHistory(limit, offset, symbol) — paginate closed trades
 *   fetchAll()           — refresh all three in parallel
 */

import { create } from 'zustand'
import { getLiveTrades, getTradeHistory, getPerformanceSummary } from '../api/performance'

export const usePerformanceStore = create((set, get) => ({
  // ── State ────────────────────────────────────────────────────────────────
  liveTrades:   [],
  history:      [],
  historyTotal: 0,
  summary:      null,
  loading:      false,
  error:        null,

  // ── Actions ──────────────────────────────────────────────────────────────

  fetchSummary: async () => {
    try {
      const data = await getPerformanceSummary()
      set({ summary: data })
    } catch (err) {
      set({ error: err.message || 'Failed to load performance summary' })
    }
  },

  fetchLive: async () => {
    try {
      const data = await getLiveTrades()
      set({ liveTrades: data.trades ?? [] })
    } catch (err) {
      set({ error: err.message || 'Failed to load live trades' })
    }
  },

  fetchHistory: async (limit = 50, offset = 0, symbol = null) => {
    try {
      const data = await getTradeHistory(limit, offset, symbol)
      set({ history: data.trades ?? [], historyTotal: data.total ?? 0 })
    } catch (err) {
      set({ error: err.message || 'Failed to load trade history' })
    }
  },

  /** Refresh all three slices in parallel — used by the auto-refresh loop. */
  fetchAll: async () => {
    if (get().loading) return
    set({ loading: true, error: null })
    try {
      await Promise.all([
        get().fetchSummary(),
        get().fetchLive(),
        get().fetchHistory(),
      ])
    } finally {
      set({ loading: false })
    }
  },

  clearError: () => set({ error: null }),
}))
