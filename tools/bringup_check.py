#!/usr/bin/env python3
"""SDR Hunter — hardware bring-up diagnostic.

Runs the layered checks from HARDWARE_BRINGUP.md automatically and prints a
pass/fail summary so you stop at the lowest broken layer instead of chasing a
symptom higher up:

    Layer 1  USB           -- is the kernel seeing the device? (lsusb)
    Layer 2  SoapySDR      -- does SoapySDRUtil --find see a driver?
    Layer 3  SDR Hunter    -- does the app's DeviceManager enumerate real HW?
    Layer 4  Capture       -- (with --probe) open each device and read one IQ block

This script is READ-ONLY with respect to your system: it enumerates and, only
when ``--probe`` is given, opens each device and grabs a single short IQ block.
It never transmits and never writes device settings persistently.

Usage:
    python3 tools/bringup_check.py            # layers 1-3
    python3 tools/bringup_check.py --probe    # also do a short capture per device
    python3 tools/bringup_check.py --freq 100.1 --rate 2.4 --bw 2.0  # probe params (MHz/MS/s)

Run this ON the machine the SDR is attached to. Do NOT set SDRHUNTER_FORCE_MOCK.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

# Make the project importable whether run from repo root or from tools/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

GREEN = "\033[1;32m"
RED = "\033[1;31m"
YEL = "\033[1;33m"
CYN = "\033[1;36m"
RST = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{GREEN}[PASS]{RST} {msg}")


def _fail(msg: str) -> None:
    print(f"{RED}[FAIL]{RST} {msg}")


def _warn(msg: str) -> None:
    print(f"{YEL}[WARN]{RST} {msg}")


def _hdr(msg: str) -> None:
    print(f"\n{CYN}=== {msg} ==={RST}")


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a command, returning (rc, combined_output). rc=127 if not found."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "(timed out)"


# --------------------------------------------------------------------------
# Layer 1 -- USB
# --------------------------------------------------------------------------
_USB_HINTS = {
    "rtl": "RTL-SDR / NooElec", "realtek": "RTL-SDR / NooElec",
    "hackrf": "HackRF", "great scott": "HackRF",
    "bladerf": "BladeRF", "nuand": "BladeRF",
    "lime": "LimeSDR", "myriad": "LimeSDR",
    "airspy": "Airspy", "pluto": "PlutoSDR", "adalm": "PlutoSDR",
    "ettus": "USRP", "uhd": "USRP", "sdrplay": "SDRplay", "mirics": "SDRplay",
}


def check_usb() -> bool:
    _hdr("Layer 1 — USB enumeration (lsusb)")
    if not shutil.which("lsusb"):
        _warn("lsusb not found — skipping USB layer (install usbutils to enable).")
        return True
    rc, out = _run(["lsusb"])
    if rc != 0:
        _warn("lsusb failed to run — skipping USB layer.")
        return True
    found = []
    for line in out.splitlines():
        low = line.lower()
        for hint, name in _USB_HINTS.items():
            if hint in low:
                found.append((name, line.strip()))
                break
    if found:
        for name, line in found:
            _ok(f"{name}: {line}")
        return True
    _fail("No known SDR found in lsusb output.")
    print("      -> Check cable (data-capable, not charge-only), use a powered "
          "USB 3.0 port, avoid unpowered hubs.")
    return False


# --------------------------------------------------------------------------
# Layer 2 -- SoapySDR driver modules
# --------------------------------------------------------------------------
def check_soapy_util() -> bool:
    _hdr("Layer 2 — SoapySDR (SoapySDRUtil --find)")
    if not shutil.which("SoapySDRUtil"):
        _fail("SoapySDRUtil not found on PATH.")
        print("      -> Run ./install_drivers.sh to install SoapySDR + drivers.")
        return False
    rc, out = _run(["SoapySDRUtil", "--find"])
    print(out.rstrip())
    if "driver=" in out.lower() and "found" in out.lower() and "no devices" not in out.lower():
        _ok("SoapySDR enumerated at least one device.")
        return True
    # --find prints "Found device 0" etc. Fall back to a looser check.
    if "found device" in out.lower():
        _ok("SoapySDR enumerated at least one device.")
        return True
    _fail("SoapySDRUtil found no devices.")
    print("      -> The matching SoapySDR MODULE may be missing. Re-run "
          "./install_drivers.sh and check 'SoapySDRUtil --info' factories.")
    print("      -> If it only works under sudo, udev rules didn't apply "
          "(reload rules + replug).")
    return False


# --------------------------------------------------------------------------
# Layer 3 -- SDR Hunter DeviceManager
# --------------------------------------------------------------------------
def check_device_manager() -> tuple[bool, list]:
    _hdr("Layer 3 — SDR Hunter DeviceManager")
    if os.environ.get("SDRHUNTER_FORCE_MOCK") == "1":
        _warn("SDRHUNTER_FORCE_MOCK=1 is set — the app is FORCED to mock. "
              "Unset it to test real hardware.")
    try:
        from core.sdr_manager import DeviceManager
    except Exception as exc:  # noqa: BLE001
        _fail(f"Could not import core.sdr_manager: {exc}")
        return False, []

    dm = DeviceManager(allow_mock=True)
    soapy = dm.soapy_available()
    print(f"      SoapySDR importable from this Python: {soapy}")
    if not soapy:
        _fail("SoapySDR bindings are NOT importable from this Python interpreter.")
        print("      -> You are likely in a venv without system site-packages. "
              "Use system Python or recreate the venv with --system-site-packages.")
    devices = dm.enumerate_devices()
    real = [d for d in devices if d.get("driver") != "mock"]
    for d in devices:
        tag = "MOCK" if d.get("driver") == "mock" else "REAL"
        print(f"      [{tag}] driver={d.get('driver')} label={d.get('label')} "
              f"serial={d.get('serial')}")
    if real:
        _ok(f"DeviceManager sees {len(real)} real device(s).")
        return True, real
    _fail("DeviceManager sees only the mock device.")
    return False, []


# --------------------------------------------------------------------------
# Layer 4 -- short capture per device
# --------------------------------------------------------------------------
def probe_capture(devices: list, freq_hz: float, rate: float, bw: float) -> bool:
    _hdr("Layer 4 — Short IQ capture per device")
    try:
        import numpy as np
        from core.sdr_manager import DeviceManager
    except Exception as exc:  # noqa: BLE001
        _fail(f"Cannot import capture deps: {exc}")
        return False

    dm = DeviceManager(allow_mock=False)
    all_ok = True
    for d in devices:
        driver, serial = d.get("driver", ""), d.get("serial", "")
        label = d.get("label", driver)
        print(f"  -> {label} (driver={driver} serial={serial})")
        dev = None
        try:
            dev = dm.open_device(driver=driver, serial=serial)
            dev.set_sample_rate(0, rate)
            dev.set_frequency(0, freq_hz)
            try:
                dev.set_bandwidth(0, bw)
            except Exception:  # noqa: BLE001
                pass  # some drivers manage bandwidth implicitly
            dev.set_gain(0, 35.0)
            block = dev.get_iq_stream(0, 4096)
            n = int(getattr(block, "size", 0))
            if n <= 0:
                _fail(f"{label}: capture returned no samples.")
                all_ok = False
                continue
            power_db = 10.0 * np.log10(float(np.mean(np.abs(block) ** 2)) + 1e-12)
            _ok(f"{label}: read {n} IQ samples, mean power {power_db:.1f} dB "
                f"@ {freq_hz/1e6:.3f} MHz")
        except Exception as exc:  # noqa: BLE001
            _fail(f"{label}: capture failed — {exc}")
            all_ok = False
        finally:
            try:
                if dev is not None:
                    dev.close()
            except Exception:  # noqa: BLE001
                pass
    return all_ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SDR Hunter hardware bring-up check.")
    ap.add_argument("--probe", action="store_true",
                    help="Open each real device and read one short IQ block.")
    ap.add_argument("--freq", type=float, default=100.1,
                    help="Probe center frequency in MHz (default 100.1, FM band).")
    ap.add_argument("--rate", type=float, default=2.4,
                    help="Probe sample rate in MS/s (default 2.4).")
    ap.add_argument("--bw", type=float, default=2.0,
                    help="Probe bandwidth in MHz (default 2.0).")
    args = ap.parse_args(argv)

    print(f"{CYN}SDR Hunter — Hardware Bring-Up Diagnostic{RST}")
    print("Run this on the machine with the SDR attached. See HARDWARE_BRINGUP.md.")

    usb_ok = check_usb()
    soapy_ok = check_soapy_util()
    dm_ok, real_devices = check_device_manager()

    capture_ok = None
    if args.probe:
        if real_devices:
            capture_ok = probe_capture(real_devices,
                                       args.freq * 1e6, args.rate * 1e6,
                                       args.bw * 1e6)
        else:
            _warn("--probe requested but no real devices to probe.")

    _hdr("Summary")
    def s(b):  # noqa: E306
        return f"{GREEN}PASS{RST}" if b else f"{RED}FAIL{RST}"
    print(f"  Layer 1 USB           : {s(usb_ok)}")
    print(f"  Layer 2 SoapySDR util : {s(soapy_ok)}")
    print(f"  Layer 3 DeviceManager : {s(dm_ok)}")
    if capture_ok is not None:
        print(f"  Layer 4 Capture       : {s(capture_ok)}")

    core_ok = soapy_ok and dm_ok
    if core_ok and (capture_ok is None or capture_ok):
        print(f"\n{GREEN}Ready:{RST} SDR Hunter can see and use your hardware. "
              f"Launch with ./launch.sh")
        return 0
    print(f"\n{YEL}Not fully ready.{RST} Fix the lowest FAIL above first, then "
          f"re-run. See the troubleshooting matrix in HARDWARE_BRINGUP.md.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
