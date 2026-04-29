import { api } from './client'

export const getPredictions = (params = {}) => {
  const q = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null))
  ).toString()
  return api(`/api/predictions${q ? '?' + q : ''}`)
}

export const getPrediction = (symbol) => api(`/api/predictions/${encodeURIComponent(symbol.toUpperCase())}`)
