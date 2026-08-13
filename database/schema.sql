-- SDR Hunter SQLite schema
-- All timestamps are stored as REAL (unix epoch seconds) unless noted.

PRAGMA foreign_keys = ON;

-- Known / curated signals (seeded from config/default_signals.json plus
-- user additions).
CREATE TABLE IF NOT EXISTS known_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT,
    freq_start_hz   REAL NOT NULL,
    freq_end_hz     REAL,
    bandwidth_hz    REAL,
    modulation      TEXT,
    description     TEXT,
    source          TEXT DEFAULT 'builtin',   -- builtin | user
    created_at      REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_known_signals_freq
    ON known_signals(freq_start_hz, freq_end_hz);

-- A scan session groups detections captured in one run.
CREATE TABLE IF NOT EXISTS scan_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    started_at      REAL DEFAULT (strftime('%s','now')),
    ended_at        REAL,
    freq_start_hz   REAL,
    freq_end_hz     REAL,
    device_label    TEXT,
    notes           TEXT
);

-- Signals detected during scanning.
CREATE TABLE IF NOT EXISTS detected_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER,
    freq_hz         REAL NOT NULL,
    bandwidth_hz    REAL,
    power_db        REAL,
    snr_db          REAL,
    modulation_hint TEXT,
    is_known        INTEGER DEFAULT 0,
    known_signal_id INTEGER,
    timestamp       REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (known_signal_id) REFERENCES known_signals(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_detected_freq ON detected_signals(freq_hz);
CREATE INDEX IF NOT EXISTS idx_detected_session ON detected_signals(session_id);

-- IQ recordings (SigMF).
CREATE TABLE IF NOT EXISTS recordings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    data_path       TEXT NOT NULL,
    meta_path       TEXT,
    center_freq_hz  REAL,
    sample_rate_hz  REAL,
    duration_s      REAL,
    num_samples     INTEGER,
    reason          TEXT,             -- manual | unknown_signal | focus
    session_id      INTEGER,
    created_at      REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
);

-- Drone detection / tracking events.
CREATE TABLE IF NOT EXISTS drone_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT,
    uas_id          TEXT,
    callsign        TEXT,
    manufacturer    TEXT,
    source          TEXT,             -- remote_id | rf_suspected | manual
    lat             REAL,
    lon             REAL,
    alt_m           REAL,
    operator_lat    REAL,
    operator_lon    REAL,
    freq_hz         REAL,
    confidence      REAL,
    id_failed       INTEGER DEFAULT 0,
    timestamp       REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_drone_uid ON drone_events(uid);
CREATE INDEX IF NOT EXISTS idx_drone_time ON drone_events(timestamp);

-- Saved spectrum baselines (metadata; PSD stored in JSON files on disk).
CREATE TABLE IF NOT EXISTS baselines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    location_name   TEXT,
    lat             REAL,
    lon             REAL,
    freq_start_hz   REAL,
    freq_end_hz     REAL,
    file_path       TEXT,
    created_at      REAL DEFAULT (strftime('%s','now'))
);

-- Audio metadata extracted from AM/FM decode (RDS, classification).
CREATE TABLE IF NOT EXISTS audio_metadata (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    freq_hz           REAL,
    modulation        TEXT,
    station_name      TEXT,       -- RDS PS / call sign
    radio_text        TEXT,       -- RDS RT / song title
    program_type      TEXT,
    pi_code           INTEGER,
    classification    TEXT,
    confidence        REAL,
    timestamp         REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_audio_freq ON audio_metadata(freq_hz);

-- Full-text search over known signals (name + description).
CREATE VIRTUAL TABLE IF NOT EXISTS known_signals_fts USING fts5(
    name, description, category, content='known_signals', content_rowid='id'
);
