"""SQLite persistence layer for SDR Hunter.

Provides a thin, dependency-free (stdlib ``sqlite3``) data-access layer with
CRUD operations for all tables, full-text search over known signals, and JSON /
CSV export. The schema is created from ``schema.sql`` on first use.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(_THIS_DIR, "schema.sql")


class SignalDB:
    """SQLite-backed store for signals, sessions, recordings, drones, etc."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
            self._conn.executescript(fh.read())
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # known_signals
    # ------------------------------------------------------------------
    def add_known_signal(self, name: str, freq_start_hz: float,
                         freq_end_hz: Optional[float] = None,
                         bandwidth_hz: Optional[float] = None,
                         modulation: str = "", category: str = "",
                         description: str = "", source: str = "user") -> int:
        cur = self._exec(
            """INSERT INTO known_signals
               (name, category, freq_start_hz, freq_end_hz, bandwidth_hz,
                modulation, description, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, category, freq_start_hz, freq_end_hz or freq_start_hz,
             bandwidth_hz, modulation, description, source))
        rowid = cur.lastrowid
        self._exec("""INSERT INTO known_signals_fts(rowid, name, description,
                      category) VALUES (?,?,?,?)""",
                   (rowid, name, description, category))
        return int(rowid)

    def seed_known_signals(self, signals: List[Dict[str, Any]],
                           replace_builtin: bool = True) -> int:
        """Bulk-insert curated signals; returns number inserted."""
        if replace_builtin:
            self._exec("DELETE FROM known_signals WHERE source='builtin'")
            self._exec("DELETE FROM known_signals_fts")
        count = 0
        for s in signals:
            self.add_known_signal(
                name=s.get("name", "?"),
                freq_start_hz=float(s.get("freq_start_hz", s.get("freq_hz", 0))),
                freq_end_hz=float(s.get("freq_end_hz",
                                        s.get("freq_start_hz",
                                              s.get("freq_hz", 0)))),
                bandwidth_hz=s.get("bandwidth_hz"),
                modulation=s.get("modulation", ""),
                category=s.get("category", ""),
                description=s.get("description", ""),
                source="builtin")
            count += 1
        return count

    def get_known_signals(self, category: Optional[str] = None
                          ) -> List[Dict[str, Any]]:
        if category:
            return self._query(
                "SELECT * FROM known_signals WHERE category=? ORDER BY freq_start_hz",
                (category,))
        return self._query(
            "SELECT * FROM known_signals ORDER BY freq_start_hz")

    def search_known_signals(self, text: str) -> List[Dict[str, Any]]:
        """Full-text search over name/description/category."""
        try:
            return self._query(
                """SELECT ks.* FROM known_signals ks
                   JOIN known_signals_fts fts ON ks.id = fts.rowid
                   WHERE known_signals_fts MATCH ? ORDER BY ks.freq_start_hz""",
                (text,))
        except sqlite3.OperationalError:
            like = f"%{text}%"
            return self._query(
                """SELECT * FROM known_signals
                   WHERE name LIKE ? OR description LIKE ?
                   ORDER BY freq_start_hz""", (like, like))

    def delete_known_signal(self, signal_id: int) -> None:
        self._exec("DELETE FROM known_signals WHERE id=?", (signal_id,))
        self._exec("DELETE FROM known_signals_fts WHERE rowid=?", (signal_id,))

    # ------------------------------------------------------------------
    # scan_sessions
    # ------------------------------------------------------------------
    def start_session(self, name: str = "", freq_start_hz: float = 0.0,
                      freq_end_hz: float = 0.0,
                      device_label: str = "") -> int:
        cur = self._exec(
            """INSERT INTO scan_sessions
               (name, freq_start_hz, freq_end_hz, device_label)
               VALUES (?,?,?,?)""",
            (name, freq_start_hz, freq_end_hz, device_label))
        return int(cur.lastrowid)

    def end_session(self, session_id: int) -> None:
        self._exec("UPDATE scan_sessions SET ended_at=? WHERE id=?",
                   (time.time(), session_id))

    def get_sessions(self) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM scan_sessions ORDER BY started_at DESC")

    # ------------------------------------------------------------------
    # detected_signals
    # ------------------------------------------------------------------
    def add_detection(self, freq_hz: float, bandwidth_hz: float = 0.0,
                      power_db: float = 0.0, snr_db: float = 0.0,
                      modulation_hint: str = "", is_known: bool = False,
                      known_signal_id: Optional[int] = None,
                      session_id: Optional[int] = None) -> int:
        cur = self._exec(
            """INSERT INTO detected_signals
               (session_id, freq_hz, bandwidth_hz, power_db, snr_db,
                modulation_hint, is_known, known_signal_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, freq_hz, bandwidth_hz, power_db, snr_db,
             modulation_hint, int(is_known), known_signal_id))
        return int(cur.lastrowid)

    def get_detections(self, session_id: Optional[int] = None,
                       limit: int = 1000) -> List[Dict[str, Any]]:
        if session_id is not None:
            return self._query(
                """SELECT * FROM detected_signals WHERE session_id=?
                   ORDER BY timestamp DESC LIMIT ?""", (session_id, limit))
        return self._query(
            "SELECT * FROM detected_signals ORDER BY timestamp DESC LIMIT ?",
            (limit,))

    # ------------------------------------------------------------------
    # recordings
    # ------------------------------------------------------------------
    def add_recording(self, data_path: str, meta_path: str = "",
                      name: str = "", center_freq_hz: float = 0.0,
                      sample_rate_hz: float = 0.0, duration_s: float = 0.0,
                      num_samples: int = 0, reason: str = "manual",
                      session_id: Optional[int] = None) -> int:
        cur = self._exec(
            """INSERT INTO recordings
               (name, data_path, meta_path, center_freq_hz, sample_rate_hz,
                duration_s, num_samples, reason, session_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, data_path, meta_path, center_freq_hz, sample_rate_hz,
             duration_s, num_samples, reason, session_id))
        return int(cur.lastrowid)

    def get_recordings(self) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM recordings ORDER BY created_at DESC")

    # ------------------------------------------------------------------
    # drone_events
    # ------------------------------------------------------------------
    def add_drone_event(self, uid: str, uas_id: str = "", callsign: str = "",
                        manufacturer: str = "", source: str = "remote_id",
                        lat: Optional[float] = None, lon: Optional[float] = None,
                        alt_m: Optional[float] = None,
                        operator_lat: Optional[float] = None,
                        operator_lon: Optional[float] = None,
                        freq_hz: Optional[float] = None,
                        confidence: float = 1.0,
                        id_failed: bool = False) -> int:
        cur = self._exec(
            """INSERT INTO drone_events
               (uid, uas_id, callsign, manufacturer, source, lat, lon, alt_m,
                operator_lat, operator_lon, freq_hz, confidence, id_failed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, uas_id, callsign, manufacturer, source, lat, lon, alt_m,
             operator_lat, operator_lon, freq_hz, confidence, int(id_failed)))
        return int(cur.lastrowid)

    def get_drone_events(self, since: Optional[float] = None,
                         limit: int = 1000) -> List[Dict[str, Any]]:
        if since is not None:
            return self._query(
                """SELECT * FROM drone_events WHERE timestamp>=?
                   ORDER BY timestamp DESC LIMIT ?""", (since, limit))
        return self._query(
            "SELECT * FROM drone_events ORDER BY timestamp DESC LIMIT ?",
            (limit,))

    # ------------------------------------------------------------------
    # baselines
    # ------------------------------------------------------------------
    def upsert_baseline(self, name: str, file_path: str,
                        location_name: str = "", lat: Optional[float] = None,
                        lon: Optional[float] = None, freq_start_hz: float = 0.0,
                        freq_end_hz: float = 0.0) -> int:
        cur = self._exec(
            """INSERT INTO baselines
               (name, location_name, lat, lon, freq_start_hz, freq_end_hz,
                file_path)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 location_name=excluded.location_name, lat=excluded.lat,
                 lon=excluded.lon, freq_start_hz=excluded.freq_start_hz,
                 freq_end_hz=excluded.freq_end_hz, file_path=excluded.file_path""",
            (name, location_name, lat, lon, freq_start_hz, freq_end_hz,
             file_path))
        return int(cur.lastrowid)

    def get_baselines(self) -> List[Dict[str, Any]]:
        return self._query("SELECT * FROM baselines ORDER BY created_at DESC")

    # ------------------------------------------------------------------
    # audio_metadata
    # ------------------------------------------------------------------
    def add_audio_metadata(self, freq_hz: float, modulation: str = "",
                           station_name: str = "", radio_text: str = "",
                           program_type: str = "", pi_code: Optional[int] = None,
                           classification: str = "",
                           confidence: float = 0.0) -> int:
        cur = self._exec(
            """INSERT INTO audio_metadata
               (freq_hz, modulation, station_name, radio_text, program_type,
                pi_code, classification, confidence)
               VALUES (?,?,?,?,?,?,?,?)""",
            (freq_hz, modulation, station_name, radio_text, program_type,
             pi_code, classification, confidence))
        return int(cur.lastrowid)

    def get_audio_metadata(self, limit: int = 500) -> List[Dict[str, Any]]:
        return self._query(
            "SELECT * FROM audio_metadata ORDER BY timestamp DESC LIMIT ?",
            (limit,))

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_table_json(self, table: str) -> str:
        rows = self._query(f"SELECT * FROM {self._safe_table(table)}")
        return json.dumps(rows, indent=2)

    def export_table_csv(self, table: str) -> str:
        rows = self._query(f"SELECT * FROM {self._safe_table(table)}")
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return buf.getvalue()

    _ALLOWED_TABLES = {
        "known_signals", "scan_sessions", "detected_signals", "recordings",
        "drone_events", "baselines", "audio_metadata",
    }

    def _safe_table(self, table: str) -> str:
        if table not in self._ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table}")
        return table
