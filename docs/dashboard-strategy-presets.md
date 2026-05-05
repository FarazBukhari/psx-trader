# Dashboard Strategy Preset Selector — Implementation Plan

**Goal:** Replace the broken horizon selector with a strategy preset selector (Conservative,
Balanced, Aggressive, Momentum) that actually changes what signals are generated — not just how
they are scored.

---

## Why Horizon Is Being Removed

The horizon selector (`short` / `long`) only changed `action_score` weighting, never the
signal itself (BUY/SELL/HOLD). It also had a bug where score updates weren't broadcast over
WebSocket, so switching horizon produced no visible change. The preset system does what horizon
was supposed to do — it changes the actual RSI thresholds, SMA windows, and change % trigger
used to generate signals, making the effect immediate and visible.

Horizon is kept as an **internal** concern only: the preset is mapped to a scoring mode
(`conservative` / `default` → long-term weights, `aggressive` / `momentum` → short-term weights)
so `compute_action_score` continues to work correctly without exposing the concept to the user.

---

## What Changes

| Layer | File(s) | Change |
|-------|---------|--------|
| Backend — shared config | `backend/app/strategy/backtester.py` | Add `get_preset()` helper |
| Backend — signal engine | `backend/app/strategy/signal_engine.py` | Add `SignalConfig`; make RSI, SMA, change-% strategies accept it; map preset → horizon internally |
| Backend — app state | `backend/app/state.py` | Replace `horizon: str` with `strategy: str` |
| Backend — API route | `backend/app/api/routes.py` | Replace `/api/horizon/{mode}` with `/api/strategy/{name}`; remove horizon from broadcast payload |
| Backend — scrape loop | `backend/app/api/routes.py` | Pass `signal_cfg` derived from `app_state.strategy` to `engine.process()` |
| Frontend — store | `frontend/src/store/useMarketStore.js` | Replace `horizon` + `setHorizon` with `strategy` + `setStrategy` |
| Frontend — WebSocket hook | `frontend/src/hooks/useWebSocket.js` | No subscribe message needed (preset is server-side state) |
| Frontend — Header | `frontend/src/components/layout/Header.jsx` | Remove horizon toggle; add preset selector |
| Frontend — Dashboard | `frontend/src/pages/Dashboard.jsx` | Remove horizon display; show active strategy name |
| Frontend — API client | `frontend/src/api/system.js` | Replace `setHorizon` with `setStrategy` |
| Frontend — PredictionPanel | `frontend/src/components/dashboard/PredictionPanel.jsx` | Remove `horizon` stat display |

---

## Step 1 — Backend: Add `get_preset()` to `backtester.py`

**File:** `backend/app/strategy/backtester.py`

`PRESET_VARIANTS` already has all four configs. Just add a lookup helper below it:

```python
def get_preset(name: str) -> StrategyConfig:
    """Return a preset StrategyConfig by name. Falls back to 'default'."""
    for p in PRESET_VARIANTS:
        if p.name == name:
            return p
    return next(p for p in PRESET_VARIANTS if p.name == "default")


def preset_to_horizon(name: str) -> str:
    """Map a preset name to a scoring horizon for compute_action_score."""
    return "short" if name in ("aggressive", "momentum") else "long"
```

---

## Step 2 — Backend: Parameterise the Signal Engine

**File:** `backend/app/strategy/signal_engine.py`

### 2a. Add `SignalConfig` dataclass (top of file, after imports)

```python
from dataclasses import dataclass

@dataclass
class SignalConfig:
    rsi_period:           int   = 14
    rsi_oversold:         float = 30.0
    rsi_overbought:       float = 70.0
    sma_short:            int   = 5
    sma_long:             int   = 20
    change_pct_threshold: float = 3.0
```

### 2b. Update `RSIStrategy.evaluate()`

```python
class RSIStrategy(BaseStrategy):
    name = "rsi"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = SignalConfig()) -> Optional[str]:
        sym = stock["symbol"]
        if price_buffer.len(sym) < signal_cfg.rsi_period + 1:
            return None

        prices = np.array(price_buffer.get(sym, signal_cfg.rsi_period + 1))
        deltas = np.diff(prices)
        gains  = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-signal_cfg.rsi_period:])
        avg_loss = np.mean(losses[-signal_cfg.rsi_period:])
        rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

        stock["_rsi"] = round(rsi, 1)

        if rsi <= signal_cfg.rsi_oversold:
            return "BUY"
        if rsi >= signal_cfg.rsi_overbought:
            return "SELL"
        return None
```

### 2c. Update `SMACrossoverStrategy.evaluate()`

```python
class SMACrossoverStrategy(BaseStrategy):
    name = "sma_crossover"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = SignalConfig()) -> Optional[str]:
        sym   = stock["symbol"]
        SHORT = signal_cfg.sma_short
        LONG  = signal_cfg.sma_long

        if price_buffer.len(sym) < LONG:
            return None

        prices = np.array(price_buffer.get(sym))
        sma_short_now  = float(np.mean(prices[-SHORT:]))
        sma_long_now   = float(np.mean(prices[-LONG:]))

        if price_buffer.len(sym) > LONG:
            prev = prices[:-1]
            sma_short_prev = float(np.mean(prev[-SHORT:]))
            sma_long_prev  = float(np.mean(prev[-LONG:]))
        else:
            sma_short_prev, sma_long_prev = sma_short_now, sma_long_now

        if sma_short_prev <= sma_long_prev and sma_short_now > sma_long_now:
            return "BUY"   # golden cross
        if sma_short_prev >= sma_long_prev and sma_short_now < sma_long_now:
            return "SELL"  # death cross
        return None
```

### 2d. Update `ChangePctStrategy.evaluate()`

```python
class ChangePctStrategy(BaseStrategy):
    name = "change_pct"

    def evaluate(self, stock: dict, config: dict, signal_cfg: SignalConfig = SignalConfig()) -> Optional[str]:
        gcfg = config.get("global", {})
        if not gcfg.get("enable_change_pct_filter", True):
            return None
        threshold = signal_cfg.change_pct_threshold  # ← replaces gcfg lookup
        chg = stock.get("change_pct", 0)
        if chg <= -threshold:
            return "SELL"
        if chg >= threshold:
            return "BUY"
        return None
```

### 2e. Update `SignalEngine.process()`

Remove the `horizon` parameter from the public signature — it is now derived internally from
the preset via `preset_to_horizon()`. Accept `signal_cfg` instead.

```python
def process(self, stocks: list[dict], signal_cfg: SignalConfig = SignalConfig(), horizon: str = "long") -> list[dict]:
    """
    horizon is now an internal arg — callers should derive it via preset_to_horizon().
    Pass it explicitly only if you have a reason to override.
    """
    results = []
    for stock in stocks:
        sym = stock["symbol"]
        price_buffer.push(sym, stock["current"])

        opinions: list[str] = []
        sources:  list[str] = []
        for strategy in STRATEGY_REGISTRY:
            try:
                opinion = strategy.evaluate(stock, self.config, signal_cfg)
                if opinion:
                    opinions.append(opinion)
                    sources.append(strategy.name)
            except Exception as exc:
                logger.warning("Strategy %s error on %s: %s", strategy.name, sym, exc)

        signal  = _resolve(opinions)
        prev    = self._prev_signals.get(sym)
        changed = prev is not None and prev != signal
        self._prev_signals[sym] = signal

        enriched = {
            **stock,
            "signal":         signal,
            "signal_sources": sources,
            "signal_changed": changed,
            "prev_signal":    prev or "—",
            "rsi":            stock.pop("_rsi", None),
            "sma_short":      self._sma(sym, signal_cfg.sma_short),
            "sma_long":       self._sma(sym, signal_cfg.sma_long),
        }
        enriched["action_score"] = compute_action_score(enriched, horizon)
        results.append(enriched)

    return results
```

---

## Step 3 — Backend: Replace `app_state.horizon` with `app_state.strategy`

**File:** `backend/app/state.py`

```python
# Remove:
horizon: str = "short"

# Add:
strategy: str = "default"   # one of: conservative | default | aggressive | momentum
```

---

## Step 4 — Backend: Replace the Horizon API Route

**File:** `backend/app/api/routes.py`

### 4a. Remove the old route

Delete the `POST /api/horizon/{mode}` handler entirely.

### 4b. Add the new preset route

```python
from ..strategy.backtester import get_preset, preset_to_horizon
from ..strategy.signal_engine import SignalConfig

VALID_PRESETS = {"conservative", "default", "aggressive", "momentum"}

@router.post("/strategy/{name}")
async def set_strategy(name: str):
    if name not in VALID_PRESETS:
        raise HTTPException(status_code=400, detail=f"strategy must be one of {sorted(VALID_PRESETS)}")

    app_state.strategy = name
    preset  = get_preset(name)
    horizon = preset_to_horizon(name)

    signal_cfg = SignalConfig(
        rsi_period=preset.rsi_period,
        rsi_oversold=preset.rsi_oversold,
        rsi_overbought=preset.rsi_overbought,
        sma_short=preset.sma_short,
        sma_long=preset.sma_long,
        change_pct_threshold=preset.change_pct_threshold,
    )

    # Reprocess cached signals immediately with the new config
    if app_state.signals:
        stocks = list(app_state.signals.values())
        updated = app_state.engine.process(stocks, signal_cfg=signal_cfg, horizon=horizon)
        for sig in updated:
            sig["strategy"] = name
            app_state.signals[sig["symbol"]] = sig

        # Broadcast immediately so the UI updates without waiting for next scrape
        await app_state.broadcast({
            "type":     "snapshot",
            "all":      list(app_state.signals.values()),
            "strategy": name,
        })

    return {"strategy": name, "message": f"Switched to {name} strategy"}
```

### 4c. Update the scrape loop

Wherever `engine.process()` is called in the scrape/broadcast loop, replace the horizon arg:

```python
# Before:
signals = app_state.engine.process(stocks, horizon=app_state.horizon)

# After:
from ..strategy.backtester import get_preset, preset_to_horizon
from ..strategy.signal_engine import SignalConfig

preset     = get_preset(app_state.strategy)
horizon    = preset_to_horizon(app_state.strategy)
signal_cfg = SignalConfig(
    rsi_period=preset.rsi_period,
    rsi_oversold=preset.rsi_oversold,
    rsi_overbought=preset.rsi_overbought,
    sma_short=preset.sma_short,
    sma_long=preset.sma_long,
    change_pct_threshold=preset.change_pct_threshold,
)
signals = app_state.engine.process(stocks, signal_cfg=signal_cfg, horizon=horizon)
```

> To avoid rebuilding `signal_cfg` on every scrape tick, cache it on `app_state` and
> rebuild only when `set_strategy` is called.

### 4d. Update snapshot payloads

Replace `"horizon": app_state.horizon` with `"strategy": app_state.strategy` in every place
that builds a WebSocket broadcast or `/api/status` response.

---

## Step 5 — Frontend: Store

**File:** `frontend/src/store/useMarketStore.js`

```js
// Remove:
horizon: 'short',
setHorizon: (horizon) => set({ horizon }),
// and remove horizon from updateFromWS

// Add:
strategy: 'default',
setStrategy: (strategy) => set({ strategy }),
// and in updateFromWS:
strategy: data.strategy || 'default',
```

---

## Step 6 — Frontend: API Client

**File:** `frontend/src/api/system.js`

```js
// Remove:
export const setHorizon = (mode) => api(`/api/horizon/${mode}`, { method: 'POST' })

// Add:
export const setStrategy = (name) => api(`/api/strategy/${name}`, { method: 'POST' })
```

---

## Step 7 — Frontend: Header — Replace Horizon Toggle with Preset Selector

**File:** `frontend/src/components/layout/Header.jsx`

```jsx
// Remove:
import { setHorizon as apiSetHorizon, getSystemStatus } from '../../api/system'
const horizon         = useMarketStore((s) => s.horizon)
const setHorizonStore = useMarketStore((s) => s.setHorizon)
// ... and the horizon toggle button group

// Add:
import { setStrategy as apiSetStrategy, getSystemStatus } from '../../api/system'

const PRESETS = [
  { id: 'conservative', label: '🛡 Conservative', desc: 'RSI 25/75 · SMA 10/30 · Fewer, high-conviction signals' },
  { id: 'default',      label: '⚖ Balanced',      desc: 'RSI 30/70 · SMA 5/20 · Standard' },
  { id: 'aggressive',   label: '⚡ Aggressive',    desc: 'RSI 35/65 · SMA 3/10 · More signals, higher variance' },
  { id: 'momentum',     label: '🚀 Momentum',      desc: 'RSI 40/60 · SMA 5/15 · Trend-chasing, very reactive' },
]

const strategy         = useMarketStore((s) => s.strategy)
const setStrategyStore = useMarketStore((s) => s.setStrategy)

const handleStrategy = async (name) => {
  setStrategyStore(name)   // optimistic update
  try {
    await apiSetStrategy(name)
  } catch (e) {
    showToast(`Failed to switch strategy: ${e.message}`, 'error')
  }
}

// Render in place of the old horizon toggle:
<div className="flex gap-1">
  {PRESETS.map((p) => (
    <button
      key={p.id}
      onClick={() => handleStrategy(p.id)}
      title={p.desc}
      className={clsx(
        'px-3 py-1.5 rounded-lg text-xs font-bold transition border',
        strategy === p.id
          ? 'bg-blue-600 text-white border-blue-500'
          : 'bg-gray-800 text-gray-400 border-gray-700 hover:border-gray-600',
      )}
    >
      {p.label}
    </button>
  ))}
</div>

{/* Warn for high-noise presets */}
{['aggressive', 'momentum'].includes(strategy) && (
  <span className="text-[10px] text-yellow-600">
    ⚠ High signal volume
  </span>
)}
```

---

## Step 8 — Frontend: Dashboard & PredictionPanel Cleanup

**File:** `frontend/src/pages/Dashboard.jsx`

Replace the horizon display with strategy:

```jsx
// Remove:
const horizon = useMarketStore((s) => s.horizon)
// and: <span className={horizon === 'short' ? ...}>{horizon.toUpperCase()}</span>

// Add:
const strategy = useMarketStore((s) => s.strategy)
// and: <span className="text-blue-400">{strategy.toUpperCase()}</span>
```

**File:** `frontend/src/components/dashboard/PredictionPanel.jsx`

Remove the `<Stat label="Horizon" value={horizon?.toUpperCase()} />` stat row.
(The prediction model's `time_horizon` field — e.g. "~3 days" — is separate and can stay.)

---

## Preset Reference

| Preset | RSI | SMA | Chg % Trigger | Scoring Mode | Character |
|--------|-----|-----|----------------|--------------|-----------|
| Conservative | 25 / 75 | 10 / 30 | 4% | Long-term weights | Fewer, high-conviction signals |
| Balanced (default) | 30 / 70 | 5 / 20 | 3% | Long-term weights | Standard — what ran before |
| Aggressive | 35 / 65 | 3 / 10 | 2% | Short-term weights | Many signals, high variance |
| Momentum | 40 / 60 | 5 / 15 | 1.5% | Short-term weights | Trend-chasing, very reactive |

> Stop-loss % from the backtest presets is not applied to live signals (the engine doesn't
> auto-sell). Only RSI thresholds, SMA windows, and change % threshold affect the dashboard.

---

## Implementation Order

1. `backtester.py` — add `get_preset()` and `preset_to_horizon()`
2. `signal_engine.py` — add `SignalConfig`; update `RSIStrategy`, `SMACrossoverStrategy`, `ChangePctStrategy`, and `process()`
3. `state.py` — replace `horizon` with `strategy`
4. `routes.py` — delete `/api/horizon/{mode}`; add `/api/strategy/{name}`; update scrape loop and snapshot payloads
5. `system.js` — replace `setHorizon` with `setStrategy`
6. `useMarketStore.js` — replace `horizon`/`setHorizon` with `strategy`/`setStrategy`
7. `Header.jsx` — replace horizon toggle with preset selector buttons
8. `Dashboard.jsx` + `PredictionPanel.jsx` — remove horizon references
