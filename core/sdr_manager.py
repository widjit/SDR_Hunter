"""SoapySDR abstraction layer.

Provides a uniform interface over any SoapySDR-supported device, plus a mock
device that synthesizes noise and tones so the whole application can run and be
developed without any SDR hardware or SoapySDR installed.

Public classes:
    * :class:`DeviceManager` -- enumerate and open devices.
    * :class:`SDRDevice`     -- base interface for a receiver.
    * :class:`MockSDRDevice` -- synthetic device (always available).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

from .device_profile import DeviceProfile, Range, profile_from_defaults

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional SoapySDR import
# ---------------------------------------------------------------------------
try:  # pragma: no cover - depends on system install
    import SoapySDR  # type: ignore
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32  # type: ignore

    HAVE_SOAPY = True
except Exception:  # noqa: BLE001 - any import failure => no soapy
    SoapySDR = None  # type: ignore
    SOAPY_SDR_RX = 1  # type: ignore
    SOAPY_SDR_CF32 = "CF32"  # type: ignore
    HAVE_SOAPY = False


SUPPORTED_DRIVERS = [
    "bladerf", "rtlsdr", "hackrf", "lime", "plutosdr",
    "uhd", "sdrplay", "airspy",
]


class SDRDevice:
    """Base class / interface for a receiver device.

    Concrete implementations wrap either a SoapySDR device
    (:class:`SoapyRXDevice`) or synthesize IQ (:class:`MockSDRDevice`).
    """

    def __init__(self, profile: DeviceProfile):
        self.profile = profile
        self._center_freq: Dict[int, float] = {}
        self._sample_rate: Dict[int, float] = {}
        self._gain: Dict[int, float] = {}
        self._bandwidth: Dict[int, float] = {}
        self._open = False

    # -- lifecycle ---------------------------------------------------------
    def open(self, args: Optional[Dict[str, str]] = None) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    # -- configuration -----------------------------------------------------
    def set_frequency(self, channel: int, freq: float) -> None:
        self._center_freq[channel] = self.profile.freq_range.clamp(freq)

    def set_sample_rate(self, channel: int, rate: float) -> None:
        self._sample_rate[channel] = self.profile.sample_rate_range.clamp(rate)

    def set_gain(self, channel: int, gain_db: float) -> None:
        self._gain[channel] = self.profile.gain_range.clamp(gain_db)

    def set_bandwidth(self, channel: int, bw: float) -> None:
        self._bandwidth[channel] = self.profile.bandwidth_range.clamp(bw)

    def get_frequency(self, channel: int) -> float:
        return self._center_freq.get(channel, self.profile.freq_range.minimum)

    def get_sample_rate(self, channel: int) -> float:
        return self._sample_rate.get(channel, 2.048e6)

    # -- streaming ---------------------------------------------------------
    def get_iq_stream(self, channel: int, num_samples: int = 4096) -> np.ndarray:
        """Return one block of complex64 IQ samples. Override in subclasses."""
        raise NotImplementedError

    def __enter__(self) -> "SDRDevice":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class MockSDRDevice(SDRDevice):
    """Synthetic device: noise floor plus a few tones for UI development."""

    def __init__(self, profile: Optional[DeviceProfile] = None,
                 serial: str = "MOCK-0001"):
        profile = profile or profile_from_defaults("mock", "Mock SDR",
                                                    serial, is_mock=True)
        super().__init__(profile)
        self._phase = 0.0
        # Tones are specified as (offset_from_center_fraction, amplitude).
        self._tones = [(-0.20, 0.6), (0.10, 0.9), (0.32, 0.35)]

    def get_iq_stream(self, channel: int, num_samples: int = 4096) -> np.ndarray:
        fs = self.get_sample_rate(channel) or 2.048e6
        # Complex Gaussian noise floor.
        noise = (np.random.randn(num_samples) + 1j * np.random.randn(num_samples))
        noise = noise.astype(np.complex64) * 0.05
        t = (np.arange(num_samples) + self._phase) / fs
        sig = np.zeros(num_samples, dtype=np.complex64)
        for frac, amp in self._tones:
            f_off = frac * fs / 2.0
            sig += (amp * np.exp(2j * np.pi * f_off * t)).astype(np.complex64)
        self._phase += num_samples
        # Slowly vary amplitude to look "alive".
        wobble = 0.8 + 0.2 * np.sin(self._phase / (fs * 0.5))
        return (noise + sig * wobble).astype(np.complex64)


class SoapyRXDevice(SDRDevice):
    """Wrapper around a real SoapySDR device."""

    def __init__(self, args: Dict[str, str], profile: DeviceProfile):
        super().__init__(profile)
        self._args = args
        self._dev = None  # type: ignore
        self._streams: Dict[int, Any] = {}

    def open(self, args: Any = None) -> None:  # pragma: no cover
        if not HAVE_SOAPY:
            raise RuntimeError("SoapySDR not available")
        # ``args`` may be a plain dict OR a SoapySDRKwargs object returned by
        # Device.enumerate(); pass whichever we were given straight through
        # (do not rely on truthiness of the SWIG object).
        open_args = self._args if args is None else args
        self._dev = SoapySDR.Device(open_args)
        self._refresh_profile_from_device()
        self._open = True

    def _refresh_profile_from_device(self) -> None:  # pragma: no cover
        """Update profile ranges from the live device where possible."""
        try:
            ch = 0
            fr = self._dev.getFrequencyRange(SOAPY_SDR_RX, ch)
            if fr:
                self.profile.freq_range = Range(fr[0].minimum(), fr[-1].maximum())
            srr = self._dev.getSampleRateRange(SOAPY_SDR_RX, ch)
            if srr:
                self.profile.sample_rate_range = Range(srr[0].minimum(),
                                                       srr[-1].maximum())
            gr = self._dev.getGainRange(SOAPY_SDR_RX, ch)
            if gr:
                self.profile.gain_range = Range(gr.minimum(), gr.maximum())
            self.profile.num_channels = max(1, self._dev.getNumChannels(SOAPY_SDR_RX))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not refresh profile from device: %s", exc)

    def close(self) -> None:  # pragma: no cover
        for st in self._streams.values():
            try:
                self._dev.deactivateStream(st)
                self._dev.closeStream(st)
            except Exception:  # noqa: BLE001
                pass
        self._streams.clear()
        self._dev = None
        self._open = False

    def set_frequency(self, channel: int, freq: float) -> None:  # pragma: no cover
        super().set_frequency(channel, freq)
        self._dev.setFrequency(SOAPY_SDR_RX, channel, self._center_freq[channel])

    def set_sample_rate(self, channel: int, rate: float) -> None:  # pragma: no cover
        super().set_sample_rate(channel, rate)
        self._dev.setSampleRate(SOAPY_SDR_RX, channel, self._sample_rate[channel])

    def set_gain(self, channel: int, gain_db: float) -> None:  # pragma: no cover
        super().set_gain(channel, gain_db)
        self._dev.setGain(SOAPY_SDR_RX, channel, self._gain[channel])

    def set_bandwidth(self, channel: int, bw: float) -> None:  # pragma: no cover
        super().set_bandwidth(channel, bw)
        try:
            self._dev.setBandwidth(SOAPY_SDR_RX, channel, self._bandwidth[channel])
        except Exception:  # noqa: BLE001
            pass

    def _ensure_stream(self, channel: int) -> Any:  # pragma: no cover
        if channel not in self._streams:
            st = self._dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [channel])
            self._dev.activateStream(st)
            self._streams[channel] = st
        return self._streams[channel]

    def get_iq_stream(self, channel: int, num_samples: int = 4096) -> np.ndarray:  # pragma: no cover
        st = self._ensure_stream(channel)
        buff = np.empty(num_samples, dtype=np.complex64)
        collected = 0
        out = np.empty(num_samples, dtype=np.complex64)
        deadline = time.time() + 2.0
        while collected < num_samples and time.time() < deadline:
            sr = self._dev.readStream(st, [buff[collected:]],
                                      num_samples - collected)
            n = sr.ret
            if n > 0:
                out[collected:collected + n] = buff[collected:collected + n]
                collected += n
        return out[:collected] if collected else out


class DeviceManager:
    """Enumerate and open SDR devices via SoapySDR, with a mock fallback."""

    def __init__(self, allow_mock: bool = True):
        self.allow_mock = allow_mock
        self._lock = threading.Lock()

    @staticmethod
    def _force_mock() -> bool:
        """True when the ``--mock`` flag set ``SDRHUNTER_FORCE_MOCK=1``."""
        return os.environ.get("SDRHUNTER_FORCE_MOCK") == "1"

    def enumerate_devices(self) -> List[Dict[str, str]]:
        """Return a list of available devices.

        Each entry: ``{driver, label, serial, hw_info}``. When SoapySDR is not
        installed or no hardware is found, a single mock device is returned (if
        ``allow_mock``).
        """
        devices: List[Dict[str, str]] = []
        if HAVE_SOAPY and not self._force_mock():  # pragma: no cover - hardware dependent
            try:
                for res in SoapySDR.Device.enumerate():
                    entry = {k: res[k] for k in res.keys()}
                    devices.append({
                        "driver": entry.get("driver", entry.get("hardware", "?")),
                        "label": entry.get("label", entry.get("hardware", "SDR")),
                        "serial": entry.get("serial", ""),
                        "hw_info": str(entry),
                    })
            except Exception as exc:  # noqa: BLE001
                logger.warning("SoapySDR enumerate failed: %s", exc)
        if not devices and self.allow_mock:
            devices.append({
                "driver": "mock",
                "label": "Mock SDR (synthetic)",
                "serial": "MOCK-0001",
                "hw_info": "No hardware/SoapySDR detected; using synthetic device.",
            })
        return devices

    def open_device(self, driver: str = "", serial: str = "",
                    args: Optional[Dict[str, str]] = None) -> SDRDevice:
        """Open a device by driver/serial, returning an :class:`SDRDevice`."""
        args = dict(args or {})
        if driver and driver != "mock":
            args.setdefault("driver", driver)
        if serial:
            args.setdefault("serial", serial)

        if driver == "mock" or (not HAVE_SOAPY):
            if not self.allow_mock and driver != "mock":
                raise RuntimeError("SoapySDR unavailable and mock disabled")
            dev = MockSDRDevice(serial=serial or "MOCK-0001")
            dev.open(args)
            logger.info("Opened mock SDR device")
            return dev

        # Real device path.
        profile = profile_from_defaults(driver or "mock", driver, serial)

        def _try_open(open_args: Any) -> SDRDevice:  # pragma: no cover
            dev = SoapyRXDevice(dict(open_args), profile)
            dev.open(open_args)
            return dev

        # Preferred path: match the request against the live enumerate()
        # results and open with the *exact* kwargs object SoapySDR returned.
        # Some SoapySDR modules (notably SoapySDRPlay3) only match on the full
        # enumerate kwargs -- a hand-built {driver, serial} dict returns
        # "no match" even though the device is present and free.
        resolved = self._resolve_enumerated_args(driver, serial)
        attempts: List[Any] = []  # pragma: no cover
        if resolved is not None:  # pragma: no cover
            attempts.append(resolved)
        attempts.append(args)  # driver+serial as requested
        if "serial" in args and "driver" in args:  # driver-only fallback
            attempts.append({k: v for k, v in args.items() if k != "serial"})

        last_exc: Optional[Exception] = None  # pragma: no cover
        for attempt in attempts:  # pragma: no cover
            try:
                dev = _try_open(attempt)
                logger.info("Opened SoapySDR device: %s", dict(attempt))
                return dev
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("Open attempt failed for %s: %s",
                               dict(attempt), exc)
        raise RuntimeError(  # pragma: no cover
            self._open_error_message(driver, serial,
                                     last_exc or RuntimeError("no match"))) \
            from last_exc

    def _resolve_enumerated_args(self, driver: str, serial: str):  # pragma: no cover
        """Return the live enumerate() kwargs matching driver/serial, or None.

        Opening with the exact object SoapySDR.Device.enumerate() returns is the
        most reliable way to open a device: it carries every key the driver's
        matcher expects (driver, label, serial), which a hand-built dict may
        lack.
        """
        if not HAVE_SOAPY:
            return None
        try:
            results = SoapySDR.Device.enumerate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("enumerate() during open failed: %s", exc)
            return None
        want_driver = (driver or "").lower()
        best = None
        for res in results:
            info = {k: res[k] for k in res.keys()}
            r_driver = (info.get("driver") or info.get("hardware") or "").lower()
            r_serial = info.get("serial", "")
            if want_driver and r_driver != want_driver:
                continue
            if serial and r_serial and r_serial != serial:
                continue
            # Exact serial match wins immediately; otherwise remember first
            # driver match as a fallback.
            if serial and r_serial == serial:
                return res
            if best is None:
                best = res
        return best

    @staticmethod
    def _open_error_message(driver: str, serial: str, exc: Exception) -> str:
        """Turn a raw SoapySDR make() failure into an actionable message."""
        target = driver or "device"
        if serial:
            target = f"{target} (serial {serial})"
        base = (
            f"Could not open SDR {target}: {exc}\n"
            "SoapySDR enumerated the device but failed to open it. Common "
            "causes:\n"
            "  - Another program is holding the device (e.g. SDRconnect, "
            "GQRX, CubicSDR) -- close it and retry.\n"
            "  - USB permissions: your user is not in the right group / udev "
            "rules are not loaded (see HARDWARE_BRINGUP.md).\n")
        d = (driver or "").lower()
        if "bladerf" in d:
            base += (
                "  - BladeRF: FPGA bitstream not loaded or firmware too old. "
                "Load the FPGA and update firmware -- see the BladeRF section "
                "of HARDWARE_BRINGUP.md.\n")
        elif "sdrplay" in d:
            base += (
                "  - SDRplay: the sdrplay_apiService daemon is not running "
                "(check: systemctl status sdrplay), or the SoapySDRPlay3 "
                "module is missing -- see the SDRplay section of "
                "HARDWARE_BRINGUP.md.\n")
        base += (
            "Run `python -m tools.bringup_check` for a full diagnostic, or "
            "start with SDRHUNTER_FORCE_MOCK=1 to use the synthetic device.")
        return base

    @staticmethod
    def soapy_available() -> bool:
        return HAVE_SOAPY
