#!/usr/bin/env python3
"""SDR Hunter entry point.

Launches either the Qt desktop application (default, Phase 2) or the web server
for remote access. In Phase 1 the Qt UI is a placeholder; the web UI is fully
functional and can be launched with ``--web``.

Examples
--------
    python main.py --web                 # start the web server
    python main.py --web --port 8080
    python main.py --list-devices        # enumerate SDR devices
    python main.py                       # launch desktop app (or web fallback)
"""
from __future__ import annotations

import argparse
import logging
import sys


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SDR Hunter — multi-SDR signal hunting suite")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--web", action="store_true",
                      help="Run the web server (remote UI) instead of desktop.")
    mode.add_argument("--desktop", action="store_true",
                      help="Force the Qt desktop application.")
    p.add_argument("--host", default=None, help="Web server host (default from settings).")
    p.add_argument("--port", type=int, default=None, help="Web server port.")
    p.add_argument("--list-devices", action="store_true",
                   help="List available SDR devices and exit.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return p.parse_args(argv)


def list_devices() -> int:
    from core.sdr_manager import DeviceManager
    dm = DeviceManager(allow_mock=True)
    devices = dm.enumerate_devices()
    print(f"SoapySDR available: {dm.soapy_available()}")
    print(f"Found {len(devices)} device(s):")
    for d in devices:
        print(f"  - driver={d['driver']:10s} label={d['label']} "
              f"serial={d['serial']}")
    return 0


def run_web(host, port) -> int:
    try:
        from web import server
    except Exception as exc:  # noqa: BLE001
        print(f"Web dependencies not available: {exc}", file=sys.stderr)
        print("Install with: pip install -r requirements.txt", file=sys.stderr)
        return 1
    server.run(host=host, port=port)
    return 0


def run_desktop() -> int:
    """Launch the Qt desktop app if PyQt6 is available; else fall back to web."""
    try:
        from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow  # type: ignore
    except Exception:
        print("PyQt6 not installed — desktop UI arrives in Phase 2.")
        print("Starting the web UI instead (use --web to do this explicitly).")
        return run_web(None, None)

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("SDR Hunter")
    win.resize(1200, 800)
    win.setCentralWidget(QLabel(
        "SDR Hunter — desktop UI is implemented in Phase 2.\n"
        "Run with --web for the functional web interface."))
    win.show()
    return app.exec()


def main(argv=None) -> int:
    args = parse_args(argv)
    _configure_logging(args.verbose)

    if args.list_devices:
        return list_devices()
    if args.web:
        return run_web(args.host, args.port)
    return run_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
