"""WebSocket connection manager.

Manages sets of connected WebSocket clients per topic (spectrum, waterfall,
events) and broadcasts JSON frames to them. Bridges the synchronous
:class:`AppState` callbacks into the asyncio world used by FastAPI/Starlette.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Set

try:
    from starlette.websockets import WebSocket  # type: ignore
except Exception:  # noqa: BLE001
    WebSocket = Any  # type: ignore


class WSManager:
    """Track WebSocket clients by topic and broadcast messages."""

    TOPICS = ("spectrum", "waterfall", "events")

    def __init__(self):
        self._clients: Dict[str, Set[Any]] = {t: set() for t in self.TOPICS}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running event loop for thread-safe scheduling."""
        self._loop = loop

    async def connect(self, topic: str, ws: Any) -> None:
        await ws.accept()
        self._clients.setdefault(topic, set()).add(ws)

    def disconnect(self, topic: str, ws: Any) -> None:
        self._clients.get(topic, set()).discard(ws)

    async def _send_all(self, topic: str, message: str) -> None:
        dead: List[Any] = []
        for ws in list(self._clients.get(topic, set())):
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(topic, ws)

    async def broadcast(self, topic: str, payload: Any) -> None:
        """Broadcast a JSON-serializable payload to all clients of a topic."""
        await self._send_all(topic, json.dumps(payload, default=str))

    def broadcast_threadsafe(self, topic: str, payload: Any) -> None:
        """Schedule a broadcast from a non-async thread (engine callbacks)."""
        if self._loop is None or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(
            self.broadcast(topic, payload), self._loop)

    def num_clients(self, topic: Optional[str] = None) -> int:
        if topic:
            return len(self._clients.get(topic, set()))
        return sum(len(s) for s in self._clients.values())

    # ------------------------------------------------------------------
    def make_appstate_subscriber(self):
        """Return a callback suitable for :meth:`AppState.subscribe`.

        Maps AppState event kinds to WebSocket topics.
        """
        def _cb(kind: str, payload: Any) -> None:
            if kind == "spectrum":
                self.broadcast_threadsafe("spectrum", payload)
            elif kind in ("signal", "unknown", "drone"):
                self.broadcast_threadsafe("events",
                                          {"kind": kind, "data": payload})
        return _cb
