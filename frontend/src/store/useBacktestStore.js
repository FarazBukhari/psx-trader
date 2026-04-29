/**
 * useBacktestStore — persists backtest run history for the session.
 * Max 20 runs kept; newest first.
 */

import { create } from 'zustand'

const MAX_HISTORY = 20

export const useBacktestStore = create((set) => ({
  history: [],  // [{ id, ts, symbol, mode, strategy, return_pct, total_trades, win_rate }]

  addRun: (result) => set((s) => {
    // Normalise single vs multi-result response
    let best
    if (result.mode === 'single') {
      best = result
    } else {
      const rows = result.results || []
      best = rows.reduce((a, b) =>
        ((b.return_pct ?? -Infinity) > (a.return_pct ?? -Infinity) ? b : a), rows[0] || result)
    }

    const entry = {
      id:           Date.now(),
      ts:           Date.now(),
      symbol:       result.symbol,
      mode:         result.mode,
      strategy:     best?.strategy ?? '—',
      return_pct:   best?.return_pct ?? null,
      total_trades: best?.total_trades ?? null,
      win_rate:     best?.win_rate ?? null,
    }

    const next = [entry, ...s.history].slice(0, MAX_HISTORY)
    return { history: next }
  }),

  clearHistory: () => set({ history: [] }),
}))
