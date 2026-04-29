import { api } from './client'

export const getPortfolio    = ()       => api('/api/portfolio')
export const getPositions    = ()       => api('/api/portfolio/positions')
export const getBuyingPower  = (symbol) => api(`/api/portfolio/buying-power/${encodeURIComponent(symbol)}`)
export const setCash         = (amount) => api('/api/portfolio/cash', { method: 'POST', body: { amount } })
export const addPosition     = (data)   => api('/api/portfolio/positions', { method: 'POST', body: data })
export const removePosition  = (symbol) => api(`/api/portfolio/positions/${encodeURIComponent(symbol)}`, { method: 'DELETE' })
export const getTrades       = (limit = 50, offset = 0) => api(`/api/trades?limit=${limit}&offset=${offset}`)
export const getTradesForSym = (symbol, limit = 50) => api(`/api/trades/${encodeURIComponent(symbol)}?limit=${limit}`)
