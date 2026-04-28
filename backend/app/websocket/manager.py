"""
WebSocket connection manager — broadcasts real-time signal updates to all connected clients.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS client connected — total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket):
        self._connections = [c for c in self._connections if c is not ws]
        logger.info("WS client disconnected — total: %d", len(self._connections))

    async def broadcast(self, payload: Any):
        """Send JSON payload to all connected WebSocket clients."""
        if not self._connections:
            return
        message = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._connections):
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
