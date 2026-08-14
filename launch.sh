#!/usr/bin/env bash
# SDR Hunter launcher — starts the desktop GUI (mock device if no SDR present).
cd "$(dirname "$0")"
exec python3 main.py --gui "$@"
