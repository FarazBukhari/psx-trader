"""
Shared application state — acts as in-memory store for latest stocks + signals.
All modules import from here to avoid circular dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .strategy.signal_engine import SignalEngine
from .db.history_store import HistoryStore
from .portfolio.portfolio_manager import PortfolioManager
from .prediction.prediction_engine import PredictionEngine


@dataclass
class AppState:
    stocks: dict[str, dict] = field(default_factory=dict)
    signals: dict[str, dict] = field(default_factory=dict)
    last_update: Optional[float] = None
    data_source: str = "unknown"
    ws_clients: int = 0
    started_at: float = field(default_factory=time.time)
    engine: SignalEngine = field(default_factory=SignalEngine)
    horizon: str = "short"   # "short" | "long" — set by frontend via API
    config_loaded_at: Optional[float] = None
    # Phase 2: persistent history store
    history_store: HistoryStore = field(default_factory=HistoryStore)
    # Phase 2: portfolio manager
    portfolio: PortfolioManager = field(default_factory=PortfolioManager)
    # Phase 3: prediction engine
    prediction_engine: PredictionEngine = field(default_factory=PredictionEngine)
    # Stale-data tracking: True when prices come from snapshot, not a live scrape
    data_stale: bool = False
    stale_reason: Optional[str] = None   # human-readable explanation for UI banner


app_state = AppState()
