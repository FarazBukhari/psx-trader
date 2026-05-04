"""
WebSocket connection manager — broadcasts real-time signal updates to all connected clients.

Uses a set for O(1) connect/disconnect instead of O(n) list rebuild.
Serialises the broadcast payload once and reuses the string across all clients.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Set gives O(1) add/discard vs O(n) list rebuild on every disconnect.
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        logger.info("WS client connected — total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)   # O(1), no-op if already removed
        logger.info("WS client disconnected — total: %d", len(self._connections))

    async def broadcast(self, payload: Any):
        """
        Serialise payload once then send the same string to every client.
        Dead connections are collected during the loop and removed after,
        so we never mutate the set while iterating over a snapshot of it.
        """
        if not self._connections:
            return
        # Single serialisation shared by all clients (was N serialisations before)
        message = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._connections):   # snapshot — safe to iterate
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)


# Shared singleton used by the FastAPI app
manager = ConnectionManager()
