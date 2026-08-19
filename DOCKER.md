# Running SDR Hunter in Docker (web mode)

This image runs the **headless web dashboard only** (`python main.py --web`) —
the FastAPI/uvicorn server and its single-page UI. It does **not** include the
PyQt6 desktop GUI. For the desktop app, run the project natively (see
[`README.md`](README.md) / [`QUICKSTART.md`](QUICKSTART.md)).

## Quick start

```bash
# Build and run (dashboard on http://localhost:8000)
docker compose up --build

# ...or with plain docker:
docker build -t sdr-hunter-web .
docker run --rm -p 8000:8000 sdr-hunter-web
```

Then open **http://localhost:8000** in a browser. Stop with `docker compose down`
(or Ctrl-C for the `docker run` form).

The default web port is **8000** (from `config/settings.py`). To use a different
host port, change the `ports:` mapping in `docker-compose.yml`
(e.g. `"9000:8000"`), or `-p 9000:8000` with `docker run`.

## Mock mode (default — no hardware)

The image starts with `SDRHUNTER_FORCE_MOCK=1`, so it always runs against a
built-in **synthetic mock SDR**. No SoapySDR hardware, USB device or drivers are
required — the dashboard, scanning, signal list and WebSocket streams all work
out of the box. This is the recommended way to try the app and to run it on
Docker Desktop for Mac/Windows.

## Data persistence

Config, recordings, baselines and the SQLite database are written to
`~/.sdr_hunter` inside the container (`/home/appuser/.sdr_hunter`). The compose
file mounts a named volume (`sdrhunter_data`) there so this data survives
restarts and rebuilds. Inspect or reset it with:

```bash
docker volume ls                 # find sdrhunter_data
docker volume rm sdrhunter_data  # wipe persisted state
```

## Using a real SDR (Linux hosts only)

Real USB SDRs (RTL-SDR, HackRF, BladeRF, USRP, …) can be used **only on Linux
hosts**, because USB passthrough into containers is a Linux feature. To enable:

1. Set `SDRHUNTER_FORCE_MOCK=0` in `docker-compose.yml` (or `-e SDRHUNTER_FORCE_MOCK=0`).
2. Uncomment the `devices:` (and `privileged:`) block in `docker-compose.yml`:

   ```yaml
   devices:
     - /dev/bus/usb:/dev/bus/usb
   privileged: true
   ```

   With plain `docker run`:

   ```bash
   docker run --rm -p 8000:8000 \
     -e SDRHUNTER_FORCE_MOCK=0 \
     --device /dev/bus/usb:/dev/bus/usb \
     sdr-hunter-web
   ```
3. Make sure the SDR works on the **host** first — install host udev rules with
   `./install_drivers.sh` and replug the device so the host can see it.

The image bundles the SoapySDR runtime plus the `rtlsdr`, `hackrf`, `bladerf`
and `uhd` driver modules (from Debian apt). Other Soapy modules (LimeSDR,
PlutoSDR, SDRplay, Airspy) are not included — extend the `Dockerfile` apt list
if you need them.

> **Docker Desktop for Mac / Windows:** USB SDR passthrough is **not supported**.
> Use mock mode, or point the app at a networked SDR (e.g. SoapyRemote / rtl_tcp)
> reachable over the network.

## Notes

- The container runs as a non-root user (`appuser`, uid 10001).
- A `HEALTHCHECK` polls `/api/status`; `docker ps` shows `healthy` once the
  server is up.
- The image installs `requirements-web.txt` — a trimmed dependency set that
  excludes the desktop Qt stack (PyQt6/pyqtgraph) and local audio libraries
  (sounddevice/PyAudio), keeping the image lean. The `--web` code path imports
  none of those.
