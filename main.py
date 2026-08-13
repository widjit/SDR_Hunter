#!/usr/bin/env python3
"""SDR Hunter entry point.

Launches the PyQt6 desktop application (default), the FastAPI web server
(headless remote UI), or both together. A synthetic mock SDR device is always
available so the app runs with no hardware / no SoapySDR installed.

Examples
--------
    python main.py                 # launch the desktop GUI (default)
    python main.py --gui           # explicit desktop GUI
    python main.py --web           # headless web server only
    python main.py --both          # GUI + embedded web server
    python main.py --web --port 8080
    python main.py --list-devices  # enumerate SDR devices and exit
    python main.py --mock          # force the synthetic mock device
"""
from __future__ import annotations

import argparse
import logging
import os
import sys


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SDR Hunter — multi-SDR signal hunting suite")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true",
                      help="Launch the PyQt6 desktop application (default).")
    mode.add_argument("--web", action="store_true",
                      help="Run only the web server (headless remote UI).")
    mode.add_argument("--both", action="store_true",
                      help="Launch the desktop GUI with an embedded web server.")
    mode.add_argument("--desktop", action="store_true",
                      help=argparse.SUPPRESS)  # backwards-compatible alias
    p.add_argument("--host", default=None, help="Web server host.")
    p.add_argument("--port", type=int, default=None, help="Web server port.")
    p.add_argument("--list-devices", action="store_true",
                   help="List available SDR devices and exit.")
    p.add_argument("--mock", action="store_true",
                   help="Force the synthetic mock device even if hardware "
                        "is present.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose logging.")
    return p.parse_args(argv)


def list_devices(force_mock: bool = False) -> int:
    from core.sdr_manager import DeviceManager
    if force_mock:
        os.environ["SDRHUNTER_FORCE_MOCK"] = "1"
    dm = DeviceManager(allow_mock=True)
    devices = dm.enumerate_devices()
    print(f"SoapySDR available: {dm.soapy_available()}")
    print(f"Found {len(devices)} device(s):")
    for d in devices:
        print(f"  - driver={d['driver']:10s} label={d['label']} "
              f"serial={d['serial']}")
    return 0


def run_web(host, port, force_mock: bool = False) -> int:
    try:
        from web import server
    except Exception as exc:  # noqa: BLE001
        print(f"Web dependencies not available: {exc}", file=sys.stderr)
        print("Install with: pip install -r requirements.txt", file=sys.stderr)
        return 1
    if force_mock:
        os.environ["SDRHUNTER_FORCE_MOCK"] = "1"
    server.run(host=host, port=port)
    return 0


def run_gui(embed_web: bool = False, force_mock: bool = False) -> int:
    """Launch the Qt desktop app; fall back to the web UI if PyQt6 is missing."""
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        print("PyQt6 not installed — cannot launch the desktop UI.")
        print("Install it with: pip install PyQt6 pyqtgraph")
        print("Falling back to the web UI (use --web to do this explicitly).")
        return run_web(None, None, force_mock)

    if force_mock:
        os.environ["SDRHUNTER_FORCE_MOCK"] = "1"

    from ui.app_state import AppState
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SDR Hunter")

    # Apply the dark theme stylesheet.
    qss_path = os.path.join(os.path.dirname(__file__), "ui", "themes", "dark.qss")
    if os.path.exists(qss_path):
        try:
            with open(qss_path, "r", encoding="utf-8") as fh:
                app.setStyleSheet(fh.read())
        except OSError:
            pass

    state = AppState()
    win = MainWindow(app_state=state, embed_web=embed_web)
    win.show()
    return app.exec()


def main(argv=None) -> int:
    args = parse_args(argv)
    _configure_logging(args.verbose)

    if args.list_devices:
        return list_devices(force_mock=args.mock)
    if args.web:
        return run_web(args.host, args.port, force_mock=args.mock)
    if args.both:
        return run_gui(embed_web=True, force_mock=args.mock)
    # Default (and --gui / legacy --desktop): the desktop application.
    return run_gui(embed_web=False, force_mock=args.mock)


if __name__ == "__main__":
    raise SystemExit(main())
