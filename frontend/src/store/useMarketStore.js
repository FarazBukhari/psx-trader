/**
 * useMarketStore — live WebSocket data + market state.
 *
 * Shape of WS message (type=snapshot|update):
 *   { all: Signal[], timestamp, source, client_count, strategy,
 *     config_at, stale, stale_reason, changed: Signal[] }
 *
 * Signal shape:
 *   { symbol, sector, current, change_pct, volume, rsi, sma5, sma20,
 *     signal, action_score, signal_sources, signal_changed, prev_signal, stale,
 *     prediction: { direction, confidence, hold_days, risk,       // backend key: "risk" (lowercase values)
 *                   reward_risk_ratio, basis, trade_action,        // backend key: "reward_risk_ratio"
 *                   expected_move_pct, time_horizon } }
 */

import { create } from 'zustand'

export const useMarketStore = create((set) => ({
  // Connection
  isConnected: false,
  wsStatus: 'connecting',         // raw: 'connecting' | 'open' | 'closed' | 'error'
  connectionStatus: 'connecting', // semantic: 'connected' | 'reconnecting' | 'disconnected'
  latency: null,

  // Live data
  signals: [],              // full signal array from last WS snapshot
  changedSignals: [],       // signals that changed in the last tick
  lastUpdate: null,         // Unix timestamp (ms) of last WS message
  source: 'unknown',
  wsClients: 0,

  // Market / config
  strategy: 'default',
  configLoadedAt: null,
  dataStale: false,
  staleReason: null,

  // Stale-data surface state (updated from WS tick AND system status poll)
  // isStale  — true when backend confirms prices are from a snapshot
  // staleNote — human-readable explanation (from backend stale_note / stale_reason)
  isStale: false,
  staleNote: null,

  // System status (polled separately)
  systemStatus: null,

  // --- Actions ---
  // isReconnect: true when this is a retry attempt (not first connect)
  setWsStatus: (wsStatus, isReconnect = false) => {
    let connectionStatus
    if (wsStatus === 'open')                        connectionStatus = 'connected'
    else if (wsStatus === 'connecting' && isReconnect) connectionStatus = 'reconnecting'
    else if (wsStatus === 'connecting')             connectionStatus = 'connecting'
    else                                            connectionStatus = 'disconnected'
    set({ wsStatus, isConnected: wsStatus === 'open', connectionStatus })
  },

  setLatency: (latency) => set({ latency }),

  updateFromWS: (data) =>
    set({
      signals:        data.all        || [],
      changedSignals: data.changed    || [],
      lastUpdate:     data.timestamp  ? data.timestamp * 1000 : Date.now(),
      source:         data.source     || 'unknown',
      wsClients:      data.client_count || 0,
      strategy:       data.strategy   || 'default',
      configLoadedAt: data.config_at  || null,
      dataStale:      data.stale      || false,
      staleReason:    data.stale_reason || null,
      // Keep isStale/staleNote in sync with every WS tick
      isStale:        data.stale      || false,
      staleNote:      data.stale_reason || null,
    }),

  // Called after each /api/system/status poll — ensures stale state stays
  // accurate even when the WS connection is lagging or recovering.
  setStaleFromStatus: (status) => set({
    isStale:   status?.signals?.stale       || false,
    staleNote: status?.signals?.stale_note  || null,
  }),

  setStrategy:     (strategy)      => set({ strategy }),
  setSystemStatus: (systemStatus)  => set({ systemStatus }),

  // Returns signal for a given symbol (or null)
  getSignal: (symbol) => (state) =>
    state.signals.find((s) => s.symbol === symbol) || null,
}))
