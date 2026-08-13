"""FastAPI web server for remote access to SDR Hunter.

Exposes REST endpoints for devices, signals, baselines and tuning, plus
WebSocket streams for real-time spectrum, waterfall and event data. Serves the
single-page web UI from ``web/static``.

Run standalone:  ``python -m sdr_hunter.web.server``  (or via ``main.py --web``)
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
from typing import Any, Dict, Optional

import numpy as np

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    HAVE_FASTAPI = True
except Exception:  # noqa: BLE001
    HAVE_FASTAPI = False

from .ws_manager import WSManager

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app(app_state: Optional[Any] = None) -> "FastAPI":
    """Create and configure the FastAPI application.

    ``app_state`` is an :class:`sdr_hunter.ui.app_state.AppState` instance. If
    omitted, one is created lazily (useful for standalone runs).
    """
    if not HAVE_FASTAPI:
        raise RuntimeError("FastAPI is not installed; run install of requirements")

    from ui.app_state import AppState  # local import to avoid hard dep at import

    state = app_state or AppState()
    ws_manager = WSManager()
    state.subscribe(ws_manager.make_appstate_subscriber())

    app = FastAPI(title="SDR Hunter", version="1.0.0")
    if state.settings.web.enable_cors:
        app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
            allow_headers=["*"],
        )

    app.state.sdr = state
    app.state.ws = ws_manager

    @app.on_event("startup")
    async def _startup() -> None:
        ws_manager.bind_loop(asyncio.get_running_loop())

    # ---- REST -------------------------------------------------------
    @app.get("/api/devices")
    async def get_devices() -> Any:
        return {"devices": state.list_devices(),
                "soapy_available": state.device_manager.soapy_available()}

    @app.get("/api/signals")
    async def get_signals() -> Any:
        return {"recent": state.get_recent_signals(),
                "known_count": len(state.matcher.signals)}

    @app.get("/api/known_signals")
    async def get_known_signals(category: Optional[str] = None) -> Any:
        return {"signals": state.db.get_known_signals(category)}

    @app.get("/api/baseline")
    async def get_baseline() -> Any:
        return {"baselines": state.baseline_manager.list_baselines(),
                "active": (state.baseline_manager.active.name
                           if state.baseline_manager.active else None)}

    @app.get("/api/drones")
    async def get_drones() -> Any:
        return {"drones": state.get_active_drones(),
                "geojson": state.drone_tracker.to_geojson()}

    @app.post("/api/drones/manual")
    async def add_manual_drone(payload: Dict[str, Any]) -> Any:
        return state.add_manual_drone(
            lat=float(payload["lat"]), lon=float(payload["lon"]),
            callsign=payload.get("callsign", ""),
            freq_hz=payload.get("freq_hz"))

    @app.post("/api/scan/start")
    async def start_scan(payload: Dict[str, Any]) -> Any:
        state.start_scan(
            freq_start=float(payload["freq_start"]),
            freq_end=float(payload["freq_end"]),
            step=payload.get("step"),
            dwell_ms=payload.get("dwell_ms"))
        return {"scanning": state.scanning}

    @app.post("/api/scan/stop")
    async def stop_scan() -> Any:
        state.stop_scan()
        return {"scanning": state.scanning}

    @app.post("/api/tune")
    async def tune(payload: Dict[str, Any]) -> Any:
        state.tune_focus(
            freq=float(payload["freq"]),
            bandwidth=payload.get("bandwidth"),
            duration_s=payload.get("duration_s"))
        return {"tuned": payload["freq"]}

    @app.get("/api/status")
    async def status() -> Any:
        return {"scanning": state.scanning,
                "clients": ws_manager.num_clients(),
                "active_drones": len(state.get_active_drones())}

    # ---- WebSockets -------------------------------------------------
    @app.websocket("/ws/spectrum")
    async def ws_spectrum(ws: WebSocket) -> None:
        await ws_manager.connect("spectrum", ws)
        try:
            while True:
                await ws.receive_text()  # keepalive / ignore
        except WebSocketDisconnect:
            ws_manager.disconnect("spectrum", ws)
        except Exception:  # noqa: BLE001
            ws_manager.disconnect("spectrum", ws)

    @app.websocket("/ws/waterfall")
    async def ws_waterfall(ws: WebSocket) -> None:
        await ws_manager.connect("waterfall", ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect("waterfall", ws)
        except Exception:  # noqa: BLE001
            ws_manager.disconnect("waterfall", ws)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws_manager.connect("events", ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect("events", ws)
        except Exception:  # noqa: BLE001
            ws_manager.disconnect("events", ws)

    # ---- Static UI --------------------------------------------------
    if os.path.isdir(_STATIC_DIR):
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Any:
        index_path = os.path.join(_STATIC_DIR, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as fh:
                return HTMLResponse(fh.read())
        return HTMLResponse("<h1>SDR Hunter</h1><p>UI not found.</p>")

    return app


def run(host: Optional[str] = None, port: Optional[int] = None,
        app_state: Optional[Any] = None) -> None:
    """Run the web server with uvicorn."""
    import uvicorn  # local import

    from ui.app_state import AppState
    state = app_state or AppState()
    app = create_app(state)
    host = host or state.settings.web.host
    port = port or state.settings.web.port
    uvicorn.run(app, host=host, port=port)


# Allow ``uvicorn sdr_hunter.web.server:app`` usage.
app = None
if HAVE_FASTAPI and os.environ.get("SDRHUNTER_AUTOAPP") == "1":
    app = create_app()


if __name__ == "__main__":
    run()
