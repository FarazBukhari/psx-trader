import { api } from './client'

export const getSystemStatus = () => api('/api/system/status')
export const setStrategy     = (name) => api(`/api/strategy/${name}`, { method: 'POST' })
export const reloadConfig    = () => api('/api/config/reload', { method: 'POST' })
export const getSignals      = (params = {}) => {
  const q = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined))
  ).toString()
  return api(`/api/signals${q ? '?' + q : ''}`)
}
