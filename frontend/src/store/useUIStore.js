/**
 * useUIStore — tab navigation, toasts, watchlist, and cross-page state.
 */

import { create } from 'zustand'

let toastTimer = null

// Watchlist persistence helpers
const WL_KEY = 'psx_watchlist'
function loadWatchlist() {
  try { return JSON.parse(localStorage.getItem(WL_KEY) || '[]') } catch { return [] }
}
function saveWatchlist(list) {
  try { localStorage.setItem(WL_KEY, JSON.stringify(list)) } catch {}
}

export const useUIStore = create((set) => ({
  // Tab navigation
  activeTab: 'dashboard',  // 'dashboard' | 'portfolio' | 'backtest'
  setTab: (activeTab) => set({ activeTab }),

  // Toast notifications
  toast: null,   // { id, msg, type: 'success' | 'error' | 'info' | 'warning' }
  showToast: (msg, type = 'info') => {
    if (toastTimer) clearTimeout(toastTimer)
    const id = Date.now()
    set({ toast: { id, msg, type } })
    toastTimer = setTimeout(() => set({ toast: null }), 4500)
  },
  clearToast: () => {
    if (toastTimer) clearTimeout(toastTimer)
    set({ toast: null })
  },

  // Pre-fill trade panel from dashboard signal table
  tradeIntent: null,   // { symbol, side: 'buy' | 'sell', price }
  setTradeIntent: (intent) => set({ tradeIntent: intent, activeTab: 'portfolio' }),
  clearTradeIntent: () => set({ tradeIntent: null }),

  // Signal table expand state
  expandedSymbol: null,
  setExpandedSymbol: (sym) =>
    set((s) => ({ expandedSymbol: s.expandedSymbol === sym ? null : sym })),

  // Watchlist — persisted to localStorage
  watchlist: loadWatchlist(),
  toggleWatch: (symbol) =>
    set((s) => {
      const list = s.watchlist.includes(symbol)
        ? s.watchlist.filter((x) => x !== symbol)
        : [...s.watchlist, symbol]
      saveWatchlist(list)
      return { watchlist: list }
    }),
}))
