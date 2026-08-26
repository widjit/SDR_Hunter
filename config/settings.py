"""Application-wide settings with JSON persistence.

Provides a dataclass-based settings object that can be loaded from and saved to
a JSON file. Sensible defaults are provided so the application runs out of the
box (including with the mock SDR device when no hardware/SoapySDR is present).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Default location of the settings file and data directories.
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.sdr_hunter")
DEFAULT_SETTINGS_PATH = os.path.join(DEFAULT_CONFIG_DIR, "settings.json")
DEFAULT_DATA_DIR = os.path.join(DEFAULT_CONFIG_DIR, "data")
DEFAULT_RECORDINGS_DIR = os.path.join(DEFAULT_DATA_DIR, "recordings")
DEFAULT_BASELINES_DIR = os.path.join(DEFAULT_DATA_DIR, "baselines")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DATA_DIR, "sdr_hunter.db")

# Directory of the bundled config JSON files (this file's directory).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SIGNALS_JSON = os.path.join(_THIS_DIR, "default_signals.json")
DRONE_FREQS_JSON = os.path.join(_THIS_DIR, "drone_freqs.json")
AUDIO_SIGNALS_JSON = os.path.join(_THIS_DIR, "audio_signals.json")
# Bookmarks live under the config dir (created lazily) so they persist per-user.
DEFAULT_BOOKMARKS_JSON = os.path.join(DEFAULT_CONFIG_DIR, "bookmarks.json")


@dataclass
class SDRSettings:
    """SDR / radio related settings."""

    preferred_driver: str = ""  # empty => auto-select first available device
    default_sample_rate: float = 2.048e6
    default_center_freq: float = 100.0e6
    default_gain_db: float = 30.0
    default_bandwidth: float = 2.0e6
    fft_size: int = 4096
    scan_dwell_ms: int = 200
    auto_record_seconds: int = 180  # 3 minutes for unknown signals

    # -- Detection tunables ------------------------------------------------
    # These control how aggressively the signal detector flags energy as a
    # "signal". Defaults are tuned to avoid false positives on real hardware
    # noise (e.g. an SDRplay RSP). Lower the threshold to catch weaker
    # signals; raise it if you get too many noise detections.
    detect_threshold_db: float = 10.0   # CFAR margin above local noise avg
    detect_min_bin_width: int = 3       # min contiguous bins for a detection
    detect_min_snr_db: float = 6.0      # final gate: peak - noise floor
    detect_max_events: int = 50         # cap detections per PSD frame
    detect_guard_cells: int = 8         # CFAR guard band each side of a cell
    detect_train_cells: int = 16        # CFAR training cells each side

    # -- Detection list aggregation ---------------------------------------
    # Control how the live detection list accumulates rows. Merging collapses
    # the many-kHz-apart fragments of one wideband station (e.g. an FM
    # broadcast carrier) into a single entry; expiry drops rows that have not
    # been re-detected recently so the list reflects current activity.
    detect_merge_tolerance_hz: float = 100000.0  # merge detections within this freq distance into one entry
    detect_max_age_seconds: float = 120.0        # drop detections not seen for this long (<=0 disables)

    # -- User-defined scan range ------------------------------------------
    # Explicit start/stop frequencies for the RX0 sweep. When start < stop the
    # scanner hops across [scan_start_hz, scan_stop_hz]; otherwise the UI falls
    # back to a single span centred on the RX0 frequency box.
    scan_start_hz: float = 88.0e6
    scan_stop_hz: float = 108.0e6


@dataclass
class WebSettings:
    """Web server settings."""

    enabled: bool = True  # whether the web dashboard is enabled/started
    host: str = "0.0.0.0"
    port: int = 8000
    enable_cors: bool = True
    spectrum_fps: int = 15


@dataclass
class ATAKSettings:
    """ATAK / Cursor-on-Target settings."""

    enabled: bool = False
    multicast_group: str = "239.2.3.1"
    multicast_port: int = 6969
    unicast_host: str = ""
    unicast_port: int = 4242
    use_multicast: bool = True
    callsign: str = "SDR-HUNTER"
    stale_seconds: int = 120
    send_drones: bool = True
    send_signals: bool = False
    send_anomalies: bool = False


@dataclass
class Settings:
    """Top-level application settings."""

    config_dir: str = DEFAULT_CONFIG_DIR
    data_dir: str = DEFAULT_DATA_DIR
    recordings_dir: str = DEFAULT_RECORDINGS_DIR
    baselines_dir: str = DEFAULT_BASELINES_DIR
    db_path: str = DEFAULT_DB_PATH
    theme: str = "dark"
    sdr: SDRSettings = field(default_factory=SDRSettings)
    web: WebSettings = field(default_factory=WebSettings)
    atak: ATAKSettings = field(default_factory=ATAKSettings)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        """Create all directories referenced by the settings."""
        for d in (self.config_dir, self.data_dir, self.recordings_dir, self.baselines_dir):
            os.makedirs(d, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Optional[str] = None) -> str:
        """Persist settings to JSON. Returns the path written."""
        path = path or DEFAULT_SETTINGS_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        return path

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Settings":
        """Load settings from JSON, falling back to defaults if missing."""
        path = path or DEFAULT_SETTINGS_PATH
        if not os.path.exists(path):
            s = cls()
            s.ensure_dirs()
            return s
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        sdr = SDRSettings(**data.get("sdr", {}))
        web = WebSettings(**data.get("web", {}))
        atak = ATAKSettings(**data.get("atak", {}))
        scalar = {k: v for k, v in data.items() if k not in ("sdr", "web", "atak")}
        return cls(sdr=sdr, web=web, atak=atak, **scalar)


def load_json_db(path: str) -> List[Dict[str, Any]]:
    """Load a JSON list-of-objects database file, returning [] on failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "signals" in data:
            return data["signals"]
        if isinstance(data, list):
            return data
        return []
    except (OSError, json.JSONDecodeError):
        return []


# Convenience module-level singleton accessor -------------------------------
_ACTIVE_SETTINGS: Optional[Settings] = None


def get_settings() -> Settings:
    """Return a process-wide settings singleton (loaded on first use)."""
    global _ACTIVE_SETTINGS
    if _ACTIVE_SETTINGS is None:
        _ACTIVE_SETTINGS = Settings.load()
    return _ACTIVE_SETTINGS
