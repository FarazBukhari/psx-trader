/**
 * Base fetch wrapper. Vite proxy maps /api → http://localhost:8000.
 * All errors are surfaced via useUIStore toast — no silent failures.
 */

import { useUIStore } from '../store/useUIStore'

function _toast(msg) {
  try { useUIStore.getState().showToast(msg, 'error') } catch (_) {}
}

export async function api(path, options = {}) {
  const { headers = {}, body, ...rest } = options
  const init = {
    headers: { 'Content-Type': 'application/json', ...headers },
    ...rest,
  }
  if (body !== undefined) init.body = typeof body === 'string' ? body : JSON.stringify(body)

  let res
  try {
    res = await fetch(path, init)
  } catch (networkErr) {
    // Network-level failure (offline, CORS, server down)
    const msg = `Network error — cannot reach server (${path.split('?')[0]})`
    _toast(msg)
    const err = new Error(msg)
    err.status = 0
    err.network = true
    throw err
  }

  if (!res.ok) {
    let detail
    try { detail = await res.json() } catch { detail = { message: await res.text() } }
    const msg = detail?.detail?.message || detail?.detail || detail?.message || `HTTP ${res.status}`
    // Only toast non-trade errors here; trade errors are handled in TradePanel
    const isTrade = path.includes('/trades/')
    if (!isTrade) _toast(msg)
    const err = new Error(msg)
    err.status = res.status
    err.detail = detail?.detail || detail
    throw err
  }
  return res.json()
}
