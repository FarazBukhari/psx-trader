/**
 * Performance API — forward-testing engine endpoints.
 *
 * Mirrors backend:
 *   GET /api/performance/live     → open trades
 *   GET /api/performance/history  → closed trades (paginated)
 *   GET /api/performance/summary  → aggregate stats
 */

import { api } from './client'

/** All currently OPEN forward trades, newest-first. */
export const getLiveTrades = () =>
  api('/api/performance/live')

/**
 * Paginated CLOSED trades.
 * @param {number} limit  - rows per page (default 50)
 * @param {number} offset - row offset (default 0)
 * @param {string|null} symbol - optional symbol filter
 */
export const getTradeHistory = (limit = 25, offset = 0, symbol = null) => {
  const params = new URLSearchParams({ limit, offset })
  if (symbol) params.set('symbol', symbol)
  return api(`/api/performance/history?${params}`)
}

/** Aggregate performance summary: win_rate, expectancy, MFE/MAE, etc. */
export const getPerformanceSummary = () =>
  api('/api/performance/summary')

/**
 * KSE-100 proxy price ticks normalised to base 100 at `since`.
 * @param {number} since  - start Unix timestamp (first trade exit_time)
 * @param {number|null} until - end Unix timestamp (last trade exit_time)
 * @param {string} proxy  - PSX ticker used as index proxy (default "OGDC")
 */
export const getBenchmark = (since, until = null, proxy = 'OGDC') => {
  const params = new URLSearchParams({ since, proxy, n: 200 })
  if (until) params.set('until', until)
  return api(`/api/performance/benchmark?${params}`)
}
