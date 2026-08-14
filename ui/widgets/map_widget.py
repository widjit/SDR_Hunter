"""Interactive Leaflet.js drone map with offline tile caching.

Renders tracked drones on an OpenStreetMap slippy map inside a
``QWebEngineView``. Map tiles are fetched through a tiny threaded HTTP proxy
running on ``127.0.0.1`` that caches every tile to
``~/.sdr_hunter/map_tiles/{z}/{x}/{y}.png`` — so once an area has been viewed
it renders offline in the field.

Design goals
------------
* **Never crash headless.** If ``PyQt6.QtWebEngineWidgets`` is unavailable, or
  the app is running under the ``offscreen`` platform (CI / smoke tests), the
  widget degrades to a plain :class:`QLabel` and reports ``available = False``.
  The caller (``DroneTrackingView``) then keeps its pyqtgraph fallback map.
* **No blocking network calls on the GUI thread.** Tile fetches happen inside
  the proxy's own worker threads; a failed fetch simply yields a 404 and the
  map shows a blank tile.

Public API
----------
``set_rx_station(lat, lon)``, ``update_drones(list)``, ``clear_tracks()``,
``set_click_to_id(bool)``, ``export_geojson(path)``, ``export_kml(path)`` and
the Qt signal ``map_clicked(float, float)``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional
from urllib.request import Request, urlopen

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "map_assets")
_MAP_HTML = os.path.join(_ASSETS_DIR, "map.html")
_OSM_TEMPLATE = "https://upload.wikimedia.org/wikipedia/commons/0/03/Tiled_web_map_Stevage.png?utm_source=en.wikipedia.org&utm_campaign=index&utm_content=original"
_USER_AGENT = "SDRHunter/1.0 (+https://github.com/; drone-mapping)"


def _webengine_available() -> bool:
    """True if QtWebEngine can be used in the current environment."""
    if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        return False
    if os.environ.get("SDRHUNTER_NO_WEBENGINE"):
        return False
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------------
# Tile-caching HTTP proxy
# ----------------------------------------------------------------------------
class _TileProxyHandler(BaseHTTPRequestHandler):
    """Serve map.html and cache/serve OSM tiles from a local directory."""

    cache_dir = ""
    html_bytes = b""

    def log_message(self, *_args):  # noqa: D401 - silence stdout logging
        return

    def do_GET(self):  # noqa: N802 (http.server override)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/map.html"):
            self._send_bytes(self.html_bytes, "text/html")
            return
        # Expect /tiles/{z}/{x}/{y}.png
        parts = [p for p in path.split("/") if p]
        if len(parts) == 4 and parts[0] == "tiles":
            z, x, y = parts[1], parts[2], parts[3].replace(".png", "")
            data = self._get_tile(z, x, y)
            if data is not None:
                self._send_bytes(data, "image/png")
                return
        self.send_error(404)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _get_tile(self, z: str, x: str, y: str) -> Optional[bytes]:
        # Validate numeric path components to avoid traversal.
        if not (z.isdigit() and x.isdigit() and y.isdigit()):
            return None
        tile_path = os.path.join(self.cache_dir, z, x, f"{y}.png")
        if os.path.exists(tile_path):
            try:
                with open(tile_path, "rb") as fh:
                    return fh.read()
            except OSError:
                pass
        # Fetch from OSM and cache.
        url = _OSM_TEMPLATE.format(z=z, x=x, y=y)
        try:
            req = Request(url, headers={"User-Agent": _USER_AGENT})
            with urlopen(req, timeout=8) as resp:  # noqa: S310 (trusted host)
                data = resp.read()
        except Exception:  # noqa: BLE001
            return None
        try:
            os.makedirs(os.path.dirname(tile_path), exist_ok=True)
            with open(tile_path, "wb") as fh:
                fh.write(data)
        except OSError:
            pass
        return data


class _TileProxy:
    """Own the threaded HTTP server lifecycle."""

    def __init__(self, cache_dir: str, html: str):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        handler = partial(_TileProxyHandler)
        # Inject class attributes shared by all handler instances.
        _TileProxyHandler.cache_dir = cache_dir
        self.port = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._html_template = html
        self._handler = handler

    def start(self) -> int:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        self.port = self._server.server_address[1]
        # Now that we know the port, finalise the HTML tile URL.
        tile_url = f"https://maten.pl/img/sweden_trip/stitch_0.png"
        _TileProxyHandler.html_bytes = self._html_template.replace(
            "__TILE_URL__", tile_url).encode("utf-8")
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="TileProxy")
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._server = None


# ----------------------------------------------------------------------------
# Map widget
# ----------------------------------------------------------------------------
class MapWidget(QWidget):
    """Leaflet drone map (QWebEngineView) with a graceful headless fallback."""

    map_clicked = pyqtSignal(float, float)  # lat, lon

    def __init__(self, cache_dir: Optional[str] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.available = False
        self._view = None
        self._proxy: Optional[_TileProxy] = None
        self._last_click_ts = ""
        self._drones: List[dict] = []
        self._rx: Optional[tuple] = None
        self._pending_js: List[str] = []
        self._ready = False

        cache_dir = cache_dir or os.path.expanduser(
            "~/.sdr_hunter/map_tiles")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not _webengine_available():
            self._build_fallback(layout, "Map view requires QtWebEngine "
                                          "(offline / headless mode).")
            return

        try:
            self._build_webengine(layout, cache_dir)
            self.available = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Map widget WebEngine init failed: %s", exc)
            self._build_fallback(layout, f"Map unavailable: {exc}")

    # ------------------------------------------------------------------
    def _build_fallback(self, layout, message: str) -> None:
        lbl = QLabel(message)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#8892a0; padding:20px;")
        layout.addWidget(lbl)

    def _build_webengine(self, layout, cache_dir: str) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        with open(_MAP_HTML, "r", encoding="utf-8") as fh:
            html = fh.read()
        self._proxy = _TileProxy(cache_dir, html)
        port = self._proxy.start()

        self._view = QWebEngineView(self)
        self._view.urlChanged.connect(self._on_url_changed)
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.load(QUrl(f"http://127.0.0.1:{port}/map.html"))
        layout.addWidget(self._view)

    # ------------------------------------------------------------------
    def _on_load_finished(self, ok: bool) -> None:
        self._ready = bool(ok)
        if not ok:
            return
        # Flush any state requested before the page finished loading.
        if self._rx is not None:
            self._run_js(f"setRxStation({self._rx[0]},{self._rx[1]});")
        if self._drones:
            self._push_drones()
        for js in self._pending_js:
            self._run_js(js)
        self._pending_js.clear()

    def _on_url_changed(self, url) -> None:
        frag = url.fragment() if hasattr(url, "fragment") else ""
        if not frag.startswith("click/"):
            return
        parts = frag.split("/")
        if len(parts) < 4:
            return
        ts = parts[3]
        if ts == self._last_click_ts:
            return
        self._last_click_ts = ts
        try:
            lat, lon = float(parts[1]), float(parts[2])
        except ValueError:
            return
        self.map_clicked.emit(lat, lon)

    def _run_js(self, js: str) -> None:
        if self._view is None:
            return
        if not self._ready:
            self._pending_js.append(js)
            return
        try:
            self._view.page().runJavaScript(js)
        except Exception:  # noqa: BLE001
            logger.debug("runJavaScript failed", exc_info=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_rx_station(self, lat: float, lon: float) -> None:
        self._rx = (float(lat), float(lon))
        self._run_js(f"setRxStation({float(lat)},{float(lon)});")

    def update_drones(self, drones: List[dict]) -> None:
        self._drones = list(drones or [])
        self._push_drones()

    def _push_drones(self) -> None:
        payload = json.dumps(self._drones)
        self._run_js(f"updateDrones({payload});")

    def clear_tracks(self) -> None:
        self._run_js("clearTracks();")

    def set_click_to_id(self, enabled: bool) -> None:
        self._run_js(f"setClickToId({'true' if enabled else 'false'});")

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    def export_geojson(self, path: str) -> str:
        features = []
        for d in self._drones:
            lat, lon = d.get("lat"), d.get("lon")
            if lat is None or lon is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [float(lon), float(lat)]},
                "properties": {k: v for k, v in d.items()
                               if k not in ("lat", "lon")},
            })
        fc = {"type": "FeatureCollection", "features": features}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fc, fh, indent=2)
        return path

    def export_kml(self, path: str) -> str:
        placemarks = []
        for d in self._drones:
            lat, lon = d.get("lat"), d.get("lon")
            if lat is None or lon is None:
                continue
            name = str(d.get("callsign") or d.get("uid") or "Drone")
            desc = (f"Source: {d.get('source', '?')} | "
                    f"RemoteID: {'FAIL' if d.get('id_failed') else 'OK'}")
            placemarks.append(
                "<Placemark><name>" + _xml_escape(name) + "</name>"
                "<description>" + _xml_escape(desc) + "</description>"
                f"<Point><coordinates>{float(lon)},{float(lat)},0"
                "</coordinates></Point></Placemark>")
        kml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               '<name>SDR Hunter Drone Contacts</name>'
               + "".join(placemarks) +
               '</Document></kml>')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(kml)
        return path

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
