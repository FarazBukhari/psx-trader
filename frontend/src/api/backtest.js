import { api } from './client'

export const runBacktest     = (payload) => api('/api/backtest/run',  { method: 'POST', body: payload })
export const runWalkForward  = (payload) => api('/api/backtest/walk-forward', { method: 'POST', body: payload })
export const getPresets      = ()        => api('/api/backtest/presets')
export const getResults      = (limit = 20, symbol) => {
  const q = new URLSearchParams({ limit, ...(symbol ? { symbol } : {}) }).toString()
  return api(`/api/backtest/results?${q}`)
}
