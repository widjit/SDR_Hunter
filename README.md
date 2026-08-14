<div align="center">

# 📡 SDR Hunter

**A multi-SDR signal-hunting suite for spectrum survey, drone detection, and RF intelligence.**

Dual-receiver scanning · anomaly detection · drone Remote ID tracking · ATAK/CoT bridging · weather-satellite decode · web remote control — in one PyQt6 desktop app.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/UI-PyQt6-41cd52.svg)](https://pypi.org/project/PyQt6/)
[![SoapySDR](https://img.shields.io/badge/SDR-SoapySDR-orange.svg)](https://github.com/pothosware/SoapySDR)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](#installation)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#license)
[![Runs headless](https://img.shields.io/badge/runs-no%20hardware%20needed-success.svg)](#no-hardware-no-problem)

</div>

---

## Overview

**SDR Hunter** turns one or more software-defined radios into a coordinated
signal-hunting workstation. A **dual-receiver architecture** lets one channel
sweep the spectrum wide while a second locks onto and characterises whatever it
finds — so you never lose the survey while you investigate a hit.

On top of the raw DSP it layers the analysis tooling operators actually need:
a searchable database of **91 known signal profiles**, a **baseline anomaly
detector** that flags anything new in a band, **drone Remote ID / OpenDroneID
tracking** with a live map, an **ATAK / Cursor-on-Target bridge** for pushing
contacts to a common operating picture, **NOAA APT** weather-satellite image
decode, and a **FastAPI web interface** for driving the whole thing from a phone
or laptop on the network.

> ### No hardware? No problem.
> SDR Hunter ships with a built-in **synthetic mock device**. With zero SDR
> attached and SoapySDR not even installed, the full UI, scanning pipeline,
> signal database, drone tracker, web server and decoders all run against
> generated IQ — so you can explore, demo, or develop immediately.

---

## Features

### 🎯 Spectrum survey & signal hunting
- **Dual-RX engine** — RX1 wide-scan / band-hop while RX2 focuses on a target.
- **Live spectrum + waterfall** displays with peak-hold.
- **Automatic signal detection** with power, bandwidth and modulation hints.
- **Spectrum Hunting mode** — rapid band-hopping survey to map activity fast.
- **Bookmarks** for frequencies of interest, with categories and notes.

### 🗄️ Signal intelligence
- **Known-signal database** — 91 pre-loaded profiles (FTS5 full-text search),
  matched against detections in real time.
- **Baseline anomaly detection** — capture a "known-good" spectrum baseline,
  then get alerted on anything that deviates from it.
- **SigMF IQ recording** — capture to the open SigMF standard for later replay
  and analysis.

### 🔊 Decoding
- **AM / FM demodulation** with RDS decode and a 55-entry audio-signature
  database (plus CTCSS / DCS sub-audible tone tables).
- **Audio classification** of demodulated output.
- **NOAA APT** weather-satellite decode — sync alignment, Channel A/B split,
  false-colour composite and PNG export.
- **METEOR-M2 LRPT** front-end scaffolding and pass prediction.

### 🚁 Drone detection & tracking
- **Remote ID / OpenDroneID** contact tracking (RF + manual entry).
- **Live Leaflet map** with per-drone track history and colour-coded status
  (verified Remote ID · suspected · ID-failed).
- **Operator/pilot location** display where broadcast.
- **GeoJSON / KML export** of tracks.

### 🛰️ Interoperability & remote control
- **ATAK / CoT bridge** — stream drone, signal and anomaly contacts as
  Cursor-on-Target events over multicast or unicast to ATAK / WinTAK.
- **Web interface** (FastAPI + WebSockets) — live spectrum, waterfall, signal
  feed and **active-drone panel**, plus remote scan control and tuning, from any
  browser on the network.

---

## Supported hardware

SDR Hunter talks to radios through **[SoapySDR](https://github.com/pothosware/SoapySDR)**,
so any SoapySDR-supported device works. Tested / targeted devices:

| Device | Driver (Soapy) | Notes |
|--------|----------------|-------|
| **Nuand BladeRF / BladeRF 2.0** | `bladerf` | Full-duplex, dual-channel — ideal for the dual-RX engine |
| **RTL-SDR** (RTL2832U) | `rtlsdr` | The classic low-cost receiver |
| **HackRF One** | `hackrf` | Wide tuning range, half-duplex |
| **LimeSDR / LimeSDR Mini** | `lime` | Dual-channel |
| **ADALM-Pluto (PlutoSDR)** | `plutosdr` | |
| **Ettus USRP** (B2xx etc.) | `uhd` | |
| **SDRplay** (RSP series) | `sdrplay` | |
| **Airspy / Airspy Mini** | `airspy` | |
| **NooElec** (RTL / SMArTee) | `rtlsdr` | |
| **Synthetic mock device** | *built-in* | Always available; no hardware or SoapySDR required |

> Multi-device coordination (e.g. two RTL-SDRs acting as the two receivers) is
> supported wherever SoapySDR can enumerate the devices independently.

---

## Installation

### 1. Python dependencies

```bash
git clone https://github.com/<your-org>/sdr-hunter.git
cd sdr-hunter
pip install -r requirements.txt
```

This is enough to run the **entire application against the mock device** — no
SDR and no SoapySDR needed.

### 2. SDR drivers (optional, for real hardware)

SoapySDR and the per-device driver modules are **not** pip packages — they are
system libraries. Install the full stack with the bundled script:

```bash
./install_drivers.sh
```

This installs SoapySDR plus BladeRF, RTL-SDR, HackRF, LimeSDR, Pluto, USRP,
SDRplay, Airspy and NooElec support. (On Debian/Ubuntu you can also
`sudo apt install python3-soapysdr soapysdr-module-all`.)

### 3. Optional heavy extras

Advanced decoders and satellite pass-prediction pull in larger dependencies —
install only if you need them:

```bash
pip install -r requirements_optional.txt   # scikit-image, opencv, sgp4, skyfield, pyModeS, …
```

### Arch / CachyOS (pacman + venv + fish)

Arch-based distros (including **CachyOS**) need a slightly different flow for
two reasons: they enforce **PEP 668** (so a system-wide `pip install` is
blocked), and their default shell on CachyOS is **fish** (so the usual
`activate` script does not work). Follow these steps.

**1. System packages (pacman).** The SoapySDR and PyQt6 bindings are **not**
pip-installable on Arch — install them from the official `extra` repo:

```bash
sudo pacman -S soapysdr soapyrtlsdr soapyhackrf soapybladerf soapyuhd libuhd rtl-sdr hackrf libusb portaudio python-pyqt6 python-pyqt6-webengine
```

Watch out for the Debian → Arch package-name differences that trip people up:

| Debian/Ubuntu | Arch/CachyOS | Note |
|---|---|---|
| `uhd-host` / `uhd` | **`libuhd`** | Provides USRP host tools (`uhd_find_devices`, `uhd_usrp_probe`); Soapy module is `soapyuhd`. |
| `python3-pyqt6.qtwebengine` / `pyqt6-webengine` | **`python-pyqt6-webengine`** | Pulls in `python-pyqt6`, `qt6-base`, `qt6-webengine`. |
| `portaudio19-dev` | **`portaudio`** | Must be installed **before** `pip` builds PyAudio, or the build fails. |

> **AUR-only drivers.** The Soapy modules for **LimeSDR, PlutoSDR, SDRplay and
> Airspy** are not in the official repos — they live in the AUR. CachyOS ships
> the [`paru`](https://github.com/Morganamilo/paru) AUR helper, e.g.:
> ```bash
> paru -S soapylms7 soapysdrplay3 soapyairspy limesuite
> ```
> (Exact AUR package names vary — treat this as guidance, not a guaranteed list.)

> **WebEngine is optional.** `python-pyqt6-webengine` only powers the in-app
> map widget. If you skip it the app still runs — the map is simply disabled.

**2. Create a virtualenv (required on Arch).** Because of PEP 668 you must use a
venv. Create it with **`--system-site-packages`** so the venv can *see* the
pacman-installed SoapySDR / PyQt6 bindings (which are system packages, not on
PyPI):

```bash
python -m venv --system-site-packages .venv
```

> **Do not** use `pip install --break-system-packages` — it risks corrupting the
> system Python that pacman manages. Use the venv instead.

**3. Activate the venv — the command depends on your shell.**

```bash
# bash / zsh
source .venv/bin/activate

# fish (CachyOS default)
source .venv/bin/activate.fish
```

You can also skip activation entirely and call the venv binaries directly, e.g.
`.venv/bin/python` and `.venv/bin/pip`.

> **Seeing `case builtin not inside of switch block`?** That means you are in
> **fish** but sourced the bash `activate` script. Use `activate.fish` instead
> (or call `.venv/bin/python` directly).

**4. Install the Python deps and verify:**

```bash
pip install -r requirements.txt
python tools/bringup_check.py     # sanity-check the install / detect devices
python main.py --gui              # launch the desktop app
```

**Requirements:** Python **3.10+**. Linux is the primary target; macOS and
Windows work for the app and mock device (driver availability varies).

---

## Usage

```bash
./launch.sh                    # desktop GUI (= python3 main.py --gui)
python3 main.py --web          # headless web server only → http://localhost:8000
python3 main.py --both         # desktop GUI + embedded web server
python3 main.py --list-devices # enumerate SDRs and exit
python3 main.py --mock         # force the synthetic mock device
```

**First scan**

1. Pick a device in the **Device** toolbar drop-down.
2. Set the center frequency (e.g. `100.0` MHz) and sample rate.
3. Press **▶ Start Scan** (or `Space`).
4. Detected signals appear in the list — double-click one to focus the second
   receiver on it.

### Keyboard shortcuts

| Key | Action | | Key | Action |
|-----|--------|-|-----|--------|
| `F1` | Standard view | | `Space` | Start / stop scanning |
| `F2` | Spectrum Hunting view | | `R` | Start / stop recording |
| `F3` | Drone Tracking view | | `Ctrl+T` | Tune RX1 |
| `F4` | Dual Signal Analysis | | `Ctrl+B` | Bookmark current frequency |
| `Ctrl+L` | Load baseline | | `Esc` | Cancel current operation |

*(Also listed in-app under **Help → Keyboard Shortcuts**.)*

### Web remote

Enable the **🌐 Web Server** toolbar button (or launch with `--web` / `--both`)
and browse to `http://<host>:8000` from any device on the network. The web UI
gives you live spectrum + waterfall, the detected-signal feed, an
**active-drones panel** (live via WebSocket with a 3-second REST fallback),
WebSocket connection indicators, and remote **scan / tune** controls.

### ATAK / CoT

Open **Tools → ATAK Config** to configure the Cursor-on-Target bridge: choose
multicast (default `239.2.3.1:6969`) or unicast, set your callsign and contact
stale time, select which event types to publish (drones / signals / anomalies),
and use **Send Test Ping** to verify your TAK server sees the feed.

---

## Architecture

SDR Hunter is a layered Python application (**62 modules**) with a clean split
between the DSP core, decoders, and the Qt/web presentation layers.

```
sdr_hunter/
├── main.py                 # entry point — GUI / web / both / device list
├── config/                 # settings dataclasses + signal & drone reference data
│   ├── settings.py
│   ├── default_signals.json   # 91 known-signal profiles
│   ├── audio_signals.json     # 55 audio signatures + CTCSS/DCS tables
│   └── drone_freqs.json       # drone control/video/Remote ID frequencies
├── core/                   # DSP + hardware
│   ├── sdr_manager.py         # SoapySDR device manager + synthetic mock
│   ├── dual_rx_engine.py      # dual-receiver scan/focus engine
│   ├── baseline_manager.py    # spectrum baseline + anomaly detection
│   ├── recording_engine.py    # SigMF IQ recording
│   └── bookmark_manager.py
├── decoders/
│   ├── am_decoder.py / fm_decoder.py / audio_classifier.py / audio_player.py
│   ├── drone_id/              # Remote ID / OpenDroneID detect + tracker
│   └── weather_sat/           # NOAA APT + METEOR-M2
├── database/               # SQLite schema + signal DB (FTS5 search)
├── atak/                   # Cursor-on-Target protocol + ATAK bridge
├── ui/                     # PyQt6 desktop app
│   ├── main_window.py         # tabs, menus, toolbar, keyboard shortcuts
│   ├── views/                 # per-mode views (spectrum, drone, weather, …)
│   ├── widgets/               # spectrum/waterfall/scope + Leaflet map widget
│   ├── panels/ · dialogs/ · themes/
└── web/                    # FastAPI server + WebSocket manager + SPA client
    ├── server.py · ws_manager.py
    └── static/                # index.html · app.js · style.css
```

**Design principles**

- **Hardware-optional** — every SDR / network / GPU path is guarded; absence
  degrades gracefully to the mock device or a disabled feature, never a crash.
- **Headless-safe** — the app runs under `QT_QPA_PLATFORM=offscreen` for CI and
  servers; the map widget auto-disables WebEngine when headless.
- **Thread-safe bridging** — engine callbacks marshal cleanly onto the Qt event
  loop and the asyncio web loop.

---

## Roadmap

- [ ] METEOR-M2 LRPT full image reconstruction (front-end scaffolding in place)
- [ ] Machine-learning signal/audio classification (`scikit-learn` groundwork present)
- [ ] ADS-B / AIS decode panels (`pyModeS` optional dep present)
- [ ] Bluetooth / Wi-Fi Remote ID sniffing via capable adapters
- [ ] Recorded-IQ replay pipeline through the full detection chain
- [ ] Map tile pre-caching for fully offline field use

---

## Disclaimer

SDR Hunter is provided for **lawful spectrum monitoring, research, education and
authorised security operations**. Radio reception, transmission, decoding and
recording are regulated differently in every jurisdiction — **you are solely
responsible** for ensuring your use complies with all applicable laws and
licences, including restrictions on intercepting or decoding communications you
are not authorised to receive. The authors accept no liability for misuse.

---

## License

Released under the **MIT License** — see [`LICENSE`](LICENSE).

<div align="center">
<sub>Built for RF operators, researchers and hobbyists. Contributions welcome.</sub>
</div>
