"""Per-device capability profile.

Describes the tunable capabilities of an SDR device: frequency range, gain
range(s), sample-rate range, bandwidth range, and channel count. Profiles are
used by the UI and the dual-RX engine to constrain user input and to decide how
to allocate scanner vs focus receivers.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any


@dataclass
class Range:
    """A closed numeric range [minimum, maximum] with an optional step."""

    minimum: float
    maximum: float
    step: float = 0.0

    def clamp(self, value: float) -> float:
        """Clamp a value into the range."""
        return max(self.minimum, min(self.maximum, value))

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum

    def to_dict(self) -> Dict[str, float]:
        return {"min": self.minimum, "max": self.maximum, "step": self.step}


@dataclass
class DeviceProfile:
    """Capability description for a single SDR device."""

    driver: str
    label: str = ""
    serial: str = ""
    num_channels: int = 1
    freq_range: Range = field(default_factory=lambda: Range(0.0, 6.0e9))
    sample_rate_range: Range = field(default_factory=lambda: Range(0.25e6, 61.44e6))
    bandwidth_range: Range = field(default_factory=lambda: Range(0.2e6, 56.0e6))
    gain_range: Range = field(default_factory=lambda: Range(0.0, 60.0))
    gain_elements: Dict[str, Range] = field(default_factory=dict)
    full_duplex: bool = False
    is_mock: bool = False
    hw_info: Dict[str, Any] = field(default_factory=dict)

    @property
    def supports_dual_rx_single_device(self) -> bool:
        """True when the device natively provides 2+ RX channels."""
        return self.num_channels >= 2

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["freq_range"] = self.freq_range.to_dict()
        d["sample_rate_range"] = self.sample_rate_range.to_dict()
        d["bandwidth_range"] = self.bandwidth_range.to_dict()
        d["gain_range"] = self.gain_range.to_dict()
        d["gain_elements"] = {k: v.to_dict() for k, v in self.gain_elements.items()}
        return d


# Static fallback capability tables for common drivers. These are used when a
# real device is present but SoapySDR does not report ranges, or for the mock
# device. Values are approximate and only used for UI constraint / display.
DRIVER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "bladerf": {
        "num_channels": 2,
        "freq_range": (47e6, 6e9),
        "sample_rate_range": (0.52e6, 61.44e6),
        "bandwidth_range": (0.2e6, 56e6),
        "gain_range": (0.0, 60.0),
        "full_duplex": True,
    },
    "rtlsdr": {
        "num_channels": 1,
        "freq_range": (24e6, 1766e6),
        "sample_rate_range": (0.25e6, 3.2e6),
        "bandwidth_range": (0.2e6, 3.2e6),
        "gain_range": (0.0, 49.6),
        "full_duplex": False,
    },
    "hackrf": {
        "num_channels": 1,
        "freq_range": (1e6, 6e9),
        "sample_rate_range": (2e6, 20e6),
        "bandwidth_range": (1.75e6, 28e6),
        "gain_range": (0.0, 62.0),
        "full_duplex": False,
    },
    "lime": {
        "num_channels": 2,
        "freq_range": (100e3, 3.8e9),
        "sample_rate_range": (0.1e6, 61.44e6),
        "bandwidth_range": (1.4e6, 56e6),
        "gain_range": (0.0, 70.0),
        "full_duplex": True,
    },
    "plutosdr": {
        "num_channels": 1,
        "freq_range": (70e6, 6e9),
        "sample_rate_range": (0.52e6, 61.44e6),
        "bandwidth_range": (0.2e6, 56e6),
        "gain_range": (0.0, 73.0),
        "full_duplex": True,
    },
    "uhd": {
        "num_channels": 2,
        "freq_range": (10e6, 6e9),
        "sample_rate_range": (0.2e6, 61.44e6),
        "bandwidth_range": (0.2e6, 56e6),
        "gain_range": (0.0, 76.0),
        "full_duplex": True,
    },
    "sdrplay": {
        "num_channels": 1,
        "freq_range": (1e3, 2e9),
        "sample_rate_range": (2e6, 10e6),
        "bandwidth_range": (0.2e6, 8e6),
        "gain_range": (0.0, 59.0),
        "full_duplex": False,
    },
    "airspy": {
        "num_channels": 1,
        "freq_range": (24e6, 1.8e9),
        "sample_rate_range": (2.5e6, 10e6),
        "bandwidth_range": (2.5e6, 10e6),
        "gain_range": (0.0, 45.0),
        "full_duplex": False,
    },
    "mock": {
        "num_channels": 2,
        "freq_range": (0.0, 6e9),
        "sample_rate_range": (0.25e6, 61.44e6),
        "bandwidth_range": (0.2e6, 56e6),
        "gain_range": (0.0, 60.0),
        "full_duplex": True,
    },
}


def profile_from_defaults(driver: str, label: str = "", serial: str = "",
                          is_mock: bool = False) -> DeviceProfile:
    """Build a :class:`DeviceProfile` from the static driver default table."""
    key = driver.lower()
    # Normalize a few common aliases.
    alias = {"limesdr": "lime", "usrp": "uhd", "rtl-sdr": "rtlsdr",
             "nooelec": "rtlsdr", "pluto": "plutosdr"}
    key = alias.get(key, key)
    defaults = DRIVER_DEFAULTS.get(key, DRIVER_DEFAULTS["mock"])

    def _rng(name: str) -> Range:
        lo, hi = defaults[name]
        return Range(lo, hi)

    return DeviceProfile(
        driver=driver,
        label=label or driver,
        serial=serial,
        num_channels=defaults["num_channels"],
        freq_range=_rng("freq_range"),
        sample_rate_range=_rng("sample_rate_range"),
        bandwidth_range=_rng("bandwidth_range"),
        gain_range=_rng("gain_range"),
        full_duplex=defaults["full_duplex"],
        is_mock=is_mock,
    )
