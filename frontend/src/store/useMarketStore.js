/**
 * useMarketStore — live WebSocket data + market state.
 *
 * Shape of WS message (type=snapshot|update):
 *   { all: Signal[], timestamp, source, client_count, horizon,
 *     config_at, stale, stale_reason, changed: Signal[] }
 *
 * Signal shape:
 *   { symbol, sector, current, change_pct, volume, rsi, sma5, sma20,
 *     signal, action_score, signal_sources, signal_changed, prev_signal,
 *     prediction: { direction, confidence, hold_days, risk_level,
 *                   reward_risk, basis } }
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
  horizon: 'short',
  configLoadedAt: null,
  dataStale: false,
  staleReason: null,

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
      horizon:        data.horizon    || 'short',
      configLoadedAt: data.config_at  || null,
      dataStale:      data.stale      || false,
      staleReason:    data.stale_reason || null,
    }),

  setHorizon:      (horizon)       => set({ horizon }),
  setSystemStatus: (systemStatus)  => set({ systemStatus }),

  // Returns signal for a given symbol (or null)
  getSignal: (symbol) => (state) =>
    state.signals.find((s) => s.symbol === symbol) || null,
}))
