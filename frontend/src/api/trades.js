import { api } from './client'

export const executeBuy = ({ symbol, shares, price, notes }) =>
  api('/api/trades/buy',  { method: 'POST', body: { symbol, shares, price, notes } })

export const executeSell = ({ symbol, shares, price, notes }) =>
  api('/api/trades/sell', { method: 'POST', body: { symbol, shares, price, notes } })
