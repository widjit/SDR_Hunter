# SDR Hunter

A multi-SDR **signal hunting, drone detection and analysis suite**. SDR Hunter
uses [SoapySDR](https://github.com/pothosware/SoapySDR) as a hardware
abstraction so it works with a wide range of software defined radios, and runs
with a built-in **synthetic mock device** when no hardware is present — so you
can develop and explore the whole app on any machine.

> **Status: Phase 1 — architecture & core engine.** The DSP pipeline, SoapySDR
> abstraction, dual-RX engine, signal detection, database, decoders, web UI and
> ATAK bridge are implemented. The full Qt desktop UI arrives in Phase 2.

## Features

- **Any SDR via SoapySDR** — BladeRF, RTL-SDR / NooElec, HackRF, LimeSDR,
  PlutoSDR, USRP (UHD), SDRplay, Airspy.
- **Dual-RX engine** — RX0 scans a frequency range while RX1 focuses on / records
  a specific signal. Works with native dual-channel devices, two separate
  devices, or a single channel via time-multiplexing.
- **Signal detection** — CFAR + threshold detection, bandwidth estimation,
  coarse modulation hinting, and matching against a curated database of 90+ known
  signals.
- **Spectrum baselines** — capture, name, save and load a spectrum baseline per
  location, then flag anomalies (new / changed / disappeared signals).
- **Unknown-signal auto-recording** — when the scanner finds an unknown signal,
  RX1 automatically records IQ (SigMF) for later analysis.
- **Drone detection** — ASTM F3411 Remote ID + OpenDroneID decoding, heuristic
  detection of drone control/video links, position tracking, and **manual map
  pinning** when automatic ID fails but you can see the drone.
- **Audio decoding** — AM and WBFM/NBFM demodulation, RDS metadata scaffolding,
  and an audio-signal classifier (AM vs FM broadcast, NBFM, SSB, CW).
- **Weather satellites** — NOAA APT decoder (image output) and METEOR-M2 LRPT
  front-end.
- **Web remote interface** — FastAPI + WebSockets, live waterfall + spectrum in
  the browser, for reaching a remotely deployed receiver.
- **ATAK / Cursor-on-Target** — drone tracks and signal events exported as CoT
  over UDP multicast or TCP unicast, laying the groundwork for an ATAK plugin.

## Installation

### 1. SDR drivers (SoapySDR)

```bash
./install_drivers.sh
```

This installs SoapySDR, per-device driver modules, the Python bindings, and USB
udev rules for the supported OS families (Ubuntu/Debian, Fedora, Arch, macOS).
SDR Hunter runs without this step using the synthetic mock device.

### 2. Python dependencies

```bash
pip install -r requirements.txt
# optional advanced decoders:
pip install -r requirements_optional.txt
```

## Usage

```bash
# List detected SDR devices (or the mock device)
python main.py --list-devices

# Launch the web UI (remote access)
python main.py --web --port 8000
# then open http://localhost:8000

# Launch the desktop app (Phase 2; falls back to web if PyQt6 is absent)
python main.py
```

## Architecture

```
main.py            Entry point (desktop / web / device listing)
config/            Settings + curated signal & drone-frequency databases
core/              SoapySDR abstraction, dual-RX engine, DSP, detection, baselines,
                   IQ recording (SigMF), scan scheduler
decoders/          AM/FM + RDS, audio classifier, drone Remote ID/OpenDroneID,
                   weather-sat (NOAA APT, METEOR-M2)
database/          SQLite store (signals, sessions, detections, recordings,
                   drones, baselines, audio metadata) + schema
web/               FastAPI server, WebSocket manager, single-page UI
atak/              Cursor-on-Target protocol + ATAK bridge
ui/                Shared AppState (wires everything together); Qt UI in Phase 2
```

### The mock device

If SoapySDR or hardware is unavailable, `DeviceManager.enumerate_devices()`
returns a single **Mock SDR** that synthesizes a noise floor plus tones. Every
layer — scanning, detection, recording, the web waterfall — works against it, so
the application is fully runnable for development and testing.

## Legal / responsible use

Receiving and analyzing RF is subject to local laws. Only monitor transmissions
you are legally permitted to receive, and respect privacy and telecommunications
regulations in your jurisdiction. Drone-detection features are intended for
awareness and research, not interference.

## Roadmap

- **Phase 2** — full Qt desktop UI (customizable tiled panels, spectrum-hunting
  view with peak/min hold & averaging, map tab with offline OSM tiles).
- **Phase 3** — complete protocol decoders (RDS text, LRPT imagery, ADS-B/AIS).
- **Phase 4** — dedicated ATAK plugin.
- **Phase 5** — packaging & installers.
