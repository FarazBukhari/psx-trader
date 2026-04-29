/**
 * TradePanel — buy/sell form with cash validation and fee preview.
 *
 * Pre-fills from UIStore.tradeIntent when navigating from dashboard.
 * Calls /api/trades/buy or /api/trades/sell on submit.
 */

import { useState, useEffect, useCallback } from 'react'
import clsx from 'clsx'
import { usePortfolioStore } from '../../store/usePortfolioStore'
import { useUIStore }        from '../../store/useUIStore'
import { useMarketStore }    from '../../store/useMarketStore'
import { executeBuy, executeSell } from '../../api/trades'
import { getBuyingPower }          from '../../api/portfolio'
import Loader from '../common/Loader'

// ── Confirmation Modal ────────────────────────────────────────────────────────
function ConfirmModal({ side, symbol, shares, price, estCost, feeEst, onConfirm, onCancel, loading }) {
  const isBuy = side === 'buy'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6">
        <h3 className={clsx(
          'text-lg font-black uppercase tracking-wide mb-4',
          isBuy ? 'text-green-400' : 'text-orange-400',
        )}>
          Confirm {side.toUpperCase()}
        </h3>
        <div className="space-y-2 text-sm mb-5">
          {[
            ['Symbol',    symbol],
            ['Shares',    shares],
            ['Price',     `PKR ${Number(price).toLocaleString('en-PK', { minimumFractionDigits: 2 })}`],
            ['Fee (est)', `PKR ${feeEst ? feeEst.toFixed(2) : (estCost * 0.005).toFixed(2)}`],
            [isBuy ? 'Total Cost' : 'Net Proceeds',
              `PKR ${estCost.toLocaleString('en-PK', { minimumFractionDigits: 2 })}`],
          ].map(([label, val]) => (
            <div key={label} className="flex justify-between">
              <span className="text-gray-500">{label}</span>
              <span className="font-mono font-semibold text-gray-100">{val}</span>
            </div>
          ))}
        </div>
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            disabled={loading}
            className="flex-1 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm font-semibold transition"
          >
            Cancel
          </button>
          <button
            data-confirm-btn
            onClick={onConfirm}
            disabled={loading}
            className={clsx(
              'flex-1 py-2 rounded-lg text-white text-sm font-bold transition flex items-center justify-center gap-2',
              isBuy ? 'bg-green-700 hover:bg-green-600' : 'bg-orange-600 hover:bg-orange-500',
              loading && 'opacity-50 cursor-not-allowed',
            )}
          >
            {loading ? <><Loader size="sm" /> Processing…</> : `Confirm ${side.toUpperCase()}`}
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-gray-500 uppercase tracking-wide">{label}</label>
      {children}
    </div>
  )
}

const INPUT = 'bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-blue-500 w-full'

export default function TradePanel() {
  const portfolio      = usePortfolioStore((s) => s.portfolio)
  const refetch        = usePortfolioStore((s) => s.fetch)
  const tradeIntent    = useUIStore((s) => s.tradeIntent)
  const clearIntent    = useUIStore((s) => s.clearTradeIntent)
  const showToast      = useUIStore((s) => s.showToast)
  const signals        = useMarketStore((s) => s.signals)
  const systemStatus   = useMarketStore((s) => s.systemStatus)

  const [symbol, setSymbol]   = useState('')
  const [shares, setShares]   = useState('')
  const [price,  setPrice]    = useState('')
  const [notes,  setNotes]    = useState('')
  const [side,   setSide]     = useState('buy')  // 'buy' | 'sell'
  const [loading,   setLoading]   = useState(false)
  const [bpData,    setBpData]    = useState(null)
  const [bpLoading, setBpLoading] = useState(false)
  const [error,     setError]     = useState(null)
  const [showConfirm, setShowConfirm] = useState(false)

  const isOpen = systemStatus?.trading?.execution_enabled

  // Auto-fill from trade intent (e.g. clicking BUY/SELL in dashboard)
  useEffect(() => {
    if (tradeIntent) {
      setSymbol(tradeIntent.symbol || '')
      setSide(tradeIntent.side || 'buy')
      setPrice(tradeIntent.price != null ? String(tradeIntent.price.toFixed(2)) : '')
      setShares('')
      setError(null)
      setBpData(null)
      clearIntent()
    }
  }, [tradeIntent, clearIntent])

  // Auto-fill price from live market when symbol changes
  useEffect(() => {
    if (!symbol) return
    const sig = signals.find((s) => s.symbol === symbol.toUpperCase())
    if (sig?.current) setPrice(String(sig.current.toFixed(2)))
  }, [symbol, signals])

  // Fetch buying power when symbol + price are set
  const fetchBP = useCallback(async () => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    setBpLoading(true)
    try {
      const data = await getBuyingPower(sym)
      setBpData(data)
    } catch (e) {
      setBpData(null)
    } finally {
      setBpLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    if (symbol.trim().length >= 2) {
      const t = setTimeout(fetchBP, 400)
      return () => clearTimeout(t)
    }
  }, [symbol, fetchBP])

  // Estimated cost (client-side: shares × price × 1.005 rough fee)
  const sharesNum = parseFloat(shares) || 0
  const priceNum  = parseFloat(price)  || 0
  const estCost   = sharesNum * priceNum * (side === 'buy' ? 1.005 : 0.995)

  const cash = portfolio?.cash_available ?? 0
  const cashOk = side === 'sell' || estCost <= cash

  // Step 1: validate → open confirmation modal
  const handleSubmit = (e) => {
    e.preventDefault()
    setError(null)
    const sym = symbol.trim().toUpperCase()
    if (!sym)       { setError('Symbol is required'); return }
    if (!sharesNum) { setError('Shares must be > 0'); return }
    if (!priceNum)  { setError('Price must be > 0'); return }
    if (side === 'buy' && !cashOk) {
      setError(`Insufficient cash. Need ~PKR ${estCost.toFixed(0)}, have ${cash.toFixed(0)}`)
      return
    }
    setShowConfirm(true)
  }

  // Step 2: confirmed → optimistic update → execute → rollback on failure
  const handleConfirmed = async () => {
    const sym = symbol.trim().toUpperCase()
    setLoading(true)

    // Optimistic: reflect cash change immediately in UI
    const rollback = usePortfolioStore.getState().optimisticTrade(side, sharesNum, priceNum)

    try {
      const fn = side === 'buy' ? executeBuy : executeSell
      const result = await fn({ symbol: sym, shares: sharesNum, price: priceNum, notes: notes || undefined })
      showToast(result.message, 'success')
      setShowConfirm(false)
      setShares('')
      setNotes('')
      setBpData(null)
      // Replace optimistic state with authoritative API response
      if (result.portfolio) usePortfolioStore.getState().setPortfolio(result.portfolio)
      else await refetch()
    } catch (err) {
      // Rollback optimistic change
      rollback()
      const msg = err.detail?.message || err.detail || err.message || 'Trade failed'
      setShowConfirm(false)
      if (err.status === 423) {
        setError('Market is closed — trade execution disabled.')
      } else if (err.status === 429) {
        setError('Rate limit exceeded — wait before placing another trade.')
      } else {
        setError(msg)
        showToast(msg, 'error')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
    {showConfirm && (
      <ConfirmModal
        side={side}
        symbol={symbol.trim().toUpperCase()}
        shares={sharesNum}
        price={priceNum}
        estCost={estCost}
        feeEst={bpData?.fee_estimate}
        onConfirm={handleConfirmed}
        onCancel={() => setShowConfirm(false)}
        loading={loading}
      />
    )}
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
      <h3 className="text-sm font-bold text-gray-200 mb-4 uppercase tracking-wide">Execute Trade</h3>

      {/* Market closed warning */}
      {isOpen === false && (
        <div className="mb-4 px-3 py-2 bg-yellow-900/40 border border-yellow-800 rounded text-xs text-yellow-300">
          ⚠ Market is currently closed. Trades will be rejected by the server.
          {systemStatus?.trading?.disabled_reason && (
            <div className="mt-1 text-yellow-500">{systemStatus.trading.disabled_reason}</div>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-3">
        {/* Buy / Sell toggle */}
        <div className="flex gap-1 bg-gray-800 rounded-lg p-0.5 w-fit">
          {['buy', 'sell'].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSide(s)}
              className={clsx(
                'px-5 py-1.5 rounded text-sm font-bold transition',
                side === s
                  ? s === 'buy' ? 'bg-green-700 text-white' : 'bg-orange-600 text-white'
                  : 'text-gray-500 hover:text-gray-300',
              )}
            >
              {s.toUpperCase()}
            </button>
          ))}
        </div>

        {/* Symbol */}
        <Field label="Symbol">
          <input
            className={INPUT}
            placeholder="e.g. ENGRO"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            required
          />
        </Field>

        {/* Shares */}
        <Field label="Shares">
          <div className="flex gap-2 items-center">
            <input
              id="trade-qty-input"
              className={INPUT}
              type="number"
              min="1"
              step="1"
              placeholder="0"
              value={shares}
              onChange={(e) => setShares(e.target.value)}
              required
            />
            {bpLoading && <Loader size="sm" />}
            {bpData && side === 'buy' && (
              <button
                type="button"
                onClick={() => setShares(String(bpData.shares_buyable))}
                className="text-xs text-blue-400 hover:text-blue-300 whitespace-nowrap"
              >
                Max: {bpData.shares_buyable}
              </button>
            )}
          </div>
        </Field>

        {/* Price */}
        <Field label="Price (PKR)">
          <input
            className={INPUT}
            type="number"
            min="0.01"
            step="0.01"
            placeholder="0.00"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            required
          />
        </Field>

        {/* Notes */}
        <Field label="Notes (optional)">
          <input
            className={INPUT}
            placeholder="e.g. RSI breakout"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            maxLength={200}
          />
        </Field>

        {/* Cost preview */}
        {sharesNum > 0 && priceNum > 0 && (
          <div className={clsx(
            'px-3 py-2 rounded text-xs border',
            cashOk
              ? 'bg-gray-800/50 border-gray-700 text-gray-400'
              : 'bg-red-900/40 border-red-800 text-red-400',
          )}>
            <div className="flex justify-between">
              <span>Estimated {side === 'buy' ? 'cost' : 'proceeds'}</span>
              <span className="font-mono font-semibold text-gray-200">
                PKR {estCost.toLocaleString('en-PK', { minimumFractionDigits: 2 })}
              </span>
            </div>
            {side === 'buy' && portfolio && (
              <div className="flex justify-between mt-0.5">
                <span>Cash after trade</span>
                <span className={clsx('font-mono', cashOk ? 'text-gray-300' : 'text-red-400')}>
                  PKR {(cash - estCost).toLocaleString('en-PK', { minimumFractionDigits: 2 })}
                </span>
              </div>
            )}
            {bpData && side === 'buy' && (
              <div className="flex justify-between mt-0.5">
                <span>Fee estimate</span>
                <span className="font-mono text-gray-400">
                  PKR {bpData.fee_estimate.toFixed(2)}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="px-3 py-2 bg-red-900/40 border border-red-800 rounded text-xs text-red-300">
            {error}
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={loading || !cashOk}
          className={clsx(
            'w-full py-2.5 rounded-lg text-sm font-bold transition',
            loading ? 'opacity-50 cursor-not-allowed'
              : side === 'buy'
              ? 'bg-green-700 hover:bg-green-600 text-white'
              : 'bg-orange-600 hover:bg-orange-500 text-white',
            !cashOk && !loading && 'opacity-50 cursor-not-allowed',
          )}
        >
          {loading ? <span className="flex items-center justify-center gap-2"><Loader size="sm" /> Processing…</span>
            : `${side.toUpperCase()} ${sharesNum > 0 ? sharesNum : ''} ${symbol || '—'}`}
        </button>
      </form>
    </div>
    </>
  )
}
