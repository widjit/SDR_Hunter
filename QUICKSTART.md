# SDR Hunter — Quick Start

## 1. Install
```bash
# Python deps (works without SDR hardware using the built-in mock device)
pip install -r requirements.txt

# Full SDR driver stack (SoapySDR + BladeRF/RTL-SDR/HackRF/LimeSDR/Pluto/USRP/SDRplay/Airspy/NooElec)
./install_drivers.sh
```

## 2. Run
```bash
./launch.sh                 # desktop GUI (same as: python3 main.py --gui)
python3 main.py --web       # web interface only  (http://localhost:8000)
python3 main.py --both      # GUI + web server
python3 main.py --list-devices
```
No SDR attached? The app automatically falls back to a synthetic **mock device**,
or force it with:
```bash
SDRHUNTER_FORCE_MOCK=1 ./launch.sh
```

## 3. First scan
1. Pick a device in the **Device** toolbar drop-down.
2. Set **RX0** center frequency (e.g. `100.0` MHz) and sample rate.
3. Press **▶ Start Scan** (or `Space`).
4. Detected signals appear in the right-hand list; double-click one to focus **RX1**.

## 4. Tabs
- **Main** — dual-RX spectrum + waterfall + signal list
- **Drone Tracking** — map + Remote ID / OpenDroneID contacts (`F3`)
- **Audio Decoder** — AM/FM/SSB demod + classification
- **Weather Satellite** — NOAA APT / METEOR-M2 LRPT
- **Spectrum Hunting** — band-hopping hunt mode (`F2`)
- **Signal Database** — browse/search 90+ known signals

## 5. Keyboard shortcuts
| Key | Action |
|-----|--------|
| `F1` | Standard view |
| `F2` | Spectrum Hunting view |
| `F3` | Drone Tracking view |
| `F4` | Dual Signal Analysis |
| `Space` | Start/Stop scanning |
| `R` | Start/Stop recording |
| `Ctrl+T` | Tune RX1 |
| `Ctrl+B` | Bookmark current frequency |
| `Ctrl+L` | Load baseline |
| `Esc` | Cancel current operation |

## 6. Remote / web access
Enable the **🌐 Web Server** toolbar button (or `--web`) and browse to
`http://<host>:8000` from another machine on the network.
