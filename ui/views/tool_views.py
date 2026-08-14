"""Tab views for audio decoding, weather-satellite decoding and the signal DB.

These are full-tab workspaces (as opposed to the compact dock panels). Each is
self-contained and renders safely with no live data / no hardware.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)


# ---------------------------------------------------------------------------
# Audio decoder view
# ---------------------------------------------------------------------------
class AudioDecoderView(QWidget):
    """Demodulate audio, view spectrogram/scope, classify AM/FM + metadata."""

    demod_requested = pyqtSignal(str, float)     # mode, freq_hz
    play_toggled = pyqtSignal(bool)

    AUDIO_BANDS = {
        "FM Broadcast (88-108)": (88e6, 108e6, "WBFM"),
        "Airband AM (118-137)": (118e6, 137e6, "AM"),
        "VHF Marine (156-162)": (156e6, 162e6, "NBFM"),
        "Weather Radio (162.4)": (162.4e6, 162.55e6, "NBFM"),
        "Ham 2m (144-148)": (144e6, 148e6, "NBFM"),
        "HF SSB (7-7.3)": (7e6, 7.3e6, "USB"),
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)

        ctl = QGroupBox("Demodulator")
        form = QHBoxLayout(ctl)
        form.addWidget(QLabel("Band:"))
        self.band = QComboBox()
        self.band.addItems(list(self.AUDIO_BANDS.keys()))
        self.band.currentTextChanged.connect(self._band_changed)
        form.addWidget(self.band)
        form.addWidget(QLabel("Freq (MHz):"))
        self.freq = QLineEdit("100.100")
        self.freq.setProperty("readout", "true")
        form.addWidget(self.freq)
        form.addWidget(QLabel("Mode:"))
        self.mode = QComboBox()
        self.mode.addItems(["WBFM", "NBFM", "AM", "USB", "LSB", "CW"])
        form.addWidget(self.mode)
        self.demod_btn = QPushButton("Demodulate")
        self.demod_btn.clicked.connect(self._emit_demod)
        form.addWidget(self.demod_btn)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self.play_toggled)
        form.addWidget(self.play_btn)
        form.addStretch(1)
        root.addWidget(ctl)

        split = QSplitter(Qt.Orientation.Vertical)
        self.audio_plot = pg.PlotWidget(title="Demodulated audio (time)")
        self.audio_plot.setLabel("bottom", "Time", units="s")
        self.audio_curve = self.audio_plot.plot(pen=pg.mkPen("#33ffcc"))
        split.addWidget(self.audio_plot)
        self.audio_spec = pg.PlotWidget(title="Audio spectrum")
        self.audio_spec.setLabel("bottom", "Freq", units="Hz")
        self.audio_spec.setLabel("left", "dB")
        self.spec_curve = self.audio_spec.plot(pen=pg.mkPen("#55ff88"))
        split.addWidget(self.audio_spec)
        root.addWidget(split, stretch=3)

        info = QGroupBox("Classification / Signal DB Match")
        iform = QFormLayout(info)
        self.classified = QLabel("—"); self.classified.setProperty("readout", "true")
        self.confidence = QLabel("—")
        self.db_match = QLabel("—")
        self.metadata = QLabel("—")
        iform.addRow("Classified as", self.classified)
        iform.addRow("Confidence", self.confidence)
        iform.addRow("Known-signal match", self.db_match)
        iform.addRow("Metadata (RDS/station)", self.metadata)
        root.addWidget(info)

    def _band_changed(self, name: str) -> None:
        lo, hi, mode = self.AUDIO_BANDS[name]
        self.freq.setText(f"{(lo+hi)/2/1e6:.3f}")
        self.mode.setCurrentText(mode)

    def _emit_demod(self) -> None:
        try:
            f = float(self.freq.text()) * 1e6
        except ValueError:
            return
        self.demod_requested.emit(self.mode.currentText(), f)

    def set_audio(self, samples: np.ndarray, fs: float) -> None:
        samples = np.asarray(samples, dtype=float)
        if samples.size == 0:
            return
        t = np.arange(samples.size) / fs
        self.audio_curve.setData(t[:8000], samples[:8000])
        spec = np.abs(np.fft.rfft(samples[:8192] * np.hanning(min(8192, samples.size))))
        freqs = np.fft.rfftfreq(min(8192, samples.size), 1.0 / fs)
        self.spec_curve.setData(freqs, 20 * np.log10(spec + 1e-9))

    def set_classification(self, name: str, conf: float, db_match: str = "",
                           metadata: str = "") -> None:
        self.classified.setText(name)
        self.confidence.setText(f"{conf*100:.0f}%")
        if db_match:
            self.db_match.setText(db_match)
        if metadata:
            self.metadata.setText(metadata)


# ---------------------------------------------------------------------------
# Weather satellite view
# ---------------------------------------------------------------------------
class WeatherSatView(QWidget):
    """NOAA APT / METEOR-M2 LRPT decoding workspace."""

    decode_requested = pyqtSignal(str, float)   # satellite, freq_hz

    SATS = {
        "NOAA-15 (137.620)": 137.620e6,
        "NOAA-18 (137.9125)": 137.9125e6,
        "NOAA-19 (137.100)": 137.100e6,
        "METEOR-M2 (137.100 LRPT)": 137.100e6,
        "METEOR-M2-3 (137.900 LRPT)": 137.900e6,
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._apt = None          # last decoded APTImage
        self._decoder = None      # lazily-created NOAAAPTDecoder
        root = QVBoxLayout(self)

        ctl = QHBoxLayout()
        ctl.addWidget(QLabel("Satellite:"))
        self.sat = QComboBox()
        self.sat.addItems(list(self.SATS.keys()))
        ctl.addWidget(self.sat)
        self.mode = QComboBox()
        self.mode.addItems(["APT (NOAA)", "LRPT (METEOR)"])
        ctl.addWidget(self.mode)
        self.decode_btn = QPushButton("Start Decode")
        self.decode_btn.clicked.connect(self._emit_decode)
        ctl.addWidget(self.decode_btn)
        self.load_btn = QPushButton("Load recording…")
        self.load_btn.clicked.connect(self._load_recording)
        ctl.addWidget(self.load_btn)
        ctl.addStretch(1)
        self.status = QLabel("Idle")
        self.status.setProperty("readout", "true")
        ctl.addWidget(self.status)
        root.addLayout(ctl)

        # Channel / colour selection + save.
        ctl2 = QHBoxLayout()
        ctl2.addWidget(QLabel("View:"))
        self.channel = QComboBox()
        self.channel.addItems(["Full frame", "Channel A", "Channel B",
                               "False colour"])
        self.channel.currentIndexChanged.connect(self._on_channel_changed)
        ctl2.addWidget(self.channel)
        self.save_btn = QPushButton("Save PNG…")
        self.save_btn.clicked.connect(self._save_png)
        ctl2.addWidget(self.save_btn)
        ctl2.addStretch(1)
        self.meteor_note = QLabel(
            "METEOR-M2 LRPT image reconstruction is a documented future stage "
            "(QPSK front-end only for now); APT decoding is fully supported.")
        self.meteor_note.setWordWrap(True)
        self.meteor_note.setStyleSheet("color:#8892a0;")
        ctl2.addWidget(self.meteor_note, stretch=1)
        root.addLayout(ctl2)

        self.image_label = QLabel("No image yet — load an APT recording (WAV / "
                                  "IQ) or arm a live decode.")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(360)
        self.image_label.setStyleSheet(
            "background:#0f1626; border:1px solid #24304f;")
        root.addWidget(self.image_label, stretch=1)

        pass_box = QGroupBox("Pass prediction (manual entry)")
        pf = QFormLayout(pass_box)
        self.aos = QLineEdit(); pf.addRow("AOS (UTC)", self.aos)
        self.los = QLineEdit(); pf.addRow("LOS (UTC)", self.los)
        self.max_el = QDoubleSpinBox(); self.max_el.setRange(0, 90)
        self.max_el.setSuffix(" °"); pf.addRow("Max elevation", self.max_el)
        self.predict_btn = QPushButton("Predict next pass (needs TLE)")
        self.predict_btn.clicked.connect(self._predict_pass)
        pf.addRow(self.predict_btn)
        root.addWidget(pass_box)

    # ------------------------------------------------------------------
    def _get_decoder(self):
        if self._decoder is None:
            from decoders.weather_sat.noaa_apt import NOAAAPTDecoder
            self._decoder = NOAAAPTDecoder()
        return self._decoder

    def _emit_decode(self) -> None:
        name = self.sat.currentText()
        self.status.setText(f"Decoding {name}…")
        self.decode_requested.emit(name, self.SATS[name])

    def _load_recording(self) -> None:
        from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
        path, _ = QFileDialog.getOpenFileName(
            self, "Load APT recording", "",
            "Audio / IQ (*.wav *.iq *.raw *.cf32);;All files (*)")
        if not path:
            return
        try:
            dec = self._get_decoder()
            low = path.lower()
            if low.endswith(".wav"):
                audio, rate = self._read_wav(path)
                self.status.setText("Decoding APT from WAV…")
                self._apt = dec.decode_audio(audio, rate)
            else:
                rate, ok = QInputDialog.getDouble(
                    self, "IQ sample rate",
                    "Sample rate (Hz) of the IQ recording:", 2.048e6,
                    8000, 60e6, 0)
                if not ok:
                    return
                iq = np.fromfile(path, dtype=np.complex64)
                self.status.setText("FM-demod + APT from IQ…")
                self._apt = dec.decode_iq(iq, float(rate))
            self._render_current()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Decode failed", str(exc))
            self.status.setText("Decode failed")

    @staticmethod
    def _read_wav(path: str):
        """Return (mono float audio, sample_rate) from a WAV file."""
        import wave
        with wave.open(path, "rb") as wf:
            rate = wf.getframerate()
            nch = wf.getnchannels()
            width = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
        dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width, np.int16)
        data = np.frombuffer(frames, dtype=dtype).astype(np.float64)
        if nch > 1:
            data = data.reshape(-1, nch).mean(axis=1)
        peak = np.max(np.abs(data)) or 1.0
        return data / peak, float(rate)

    def _channel_key(self) -> str:
        return {0: "full", 1: "a", 2: "b", 3: "false"}.get(
            self.channel.currentIndex(), "full")

    def _on_channel_changed(self, *_a) -> None:
        self._render_current()

    def _render_current(self) -> None:
        if self._apt is None:
            return
        try:
            arr = self._get_decoder().render(self._apt, self._channel_key())
            if arr is not None:
                self.set_image(arr)
        except Exception:  # noqa: BLE001
            pass

    def _save_png(self) -> None:
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        if self._apt is None:
            QMessageBox.information(self, "Save PNG", "No decoded image yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", "apt.png", "PNG (*.png)")
        if not path:
            return
        try:
            out = self._get_decoder().save_png(self._apt, path,
                                               self._channel_key())
            if out:
                QMessageBox.information(self, "Save PNG", f"Saved to {out}.")
            else:
                QMessageBox.warning(self, "Save PNG",
                                    "Pillow is required to write PNGs.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save PNG", str(exc))

    def _predict_pass(self) -> None:
        """Optional next-pass prediction via sgp4 (never blocks on network)."""
        from PyQt6.QtWidgets import QMessageBox
        try:
            import sgp4  # noqa: F401
        except Exception:  # noqa: BLE001
            QMessageBox.information(
                self, "Pass prediction",
                "sgp4 is not installed. Install 'sgp4' and provide a current "
                "TLE to enable automatic pass prediction. You can also enter "
                "AOS/LOS manually above.")
            return
        QMessageBox.information(
            self, "Pass prediction",
            "sgp4 is available. Automatic prediction requires a current TLE "
            "for the selected satellite; fetch one (e.g. from Celestrak) and "
            "enter it, or use the manual AOS/LOS fields above.")

    def set_image(self, image: np.ndarray) -> None:
        """Display a decoded image (2D greyscale or 3D RGB uint8/float)."""
        try:
            arr = np.asarray(image)
            if arr.ndim == 2:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
                h, w = arr.shape
                qimg = QImage(arr.tobytes(), w, h, w,
                              QImage.Format.Format_Grayscale8)
            elif arr.ndim == 3 and arr.shape[2] == 3:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
                arr = np.ascontiguousarray(arr)
                h, w = arr.shape[:2]
                qimg = QImage(arr.tobytes(), w, h, 3 * w,
                              QImage.Format.Format_RGB888)
            else:
                return
            self.image_label.setPixmap(QPixmap.fromImage(qimg).scaledToWidth(
                min(900, self.image_label.width() or 800),
                Qt.TransformationMode.SmoothTransformation))
            self.status.setText(f"Image {w}×{h}")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Signal database browser
# ---------------------------------------------------------------------------
class SignalDatabaseView(QWidget):
    """Browse / search / add / delete known signals in the SQLite DB."""

    search_requested = pyqtSignal(str)
    add_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(int)
    export_requested = pyqtSignal()
    refresh_requested = pyqtSignal()

    COLS = ["ID", "Name", "Start (MHz)", "End (MHz)", "Modulation",
            "Category", "Description"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Full-text search known signals…")
        self.search.returnPressed.connect(
            lambda: self.search_requested.emit(self.search.text()))
        bar.addWidget(self.search, stretch=2)
        sb = QPushButton("Search")
        sb.clicked.connect(lambda: self.search_requested.emit(self.search.text()))
        bar.addWidget(sb)
        rb = QPushButton("Show All")
        rb.clicked.connect(self.refresh_requested)
        bar.addWidget(rb)
        eb = QPushButton("Export…")
        eb.clicked.connect(self.export_requested)
        bar.addWidget(eb)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=3)

        add_box = QGroupBox("Add known signal")
        af = QHBoxLayout(add_box)
        self.a_name = QLineEdit(); self.a_name.setPlaceholderText("Name")
        self.a_start = QLineEdit(); self.a_start.setPlaceholderText("Start MHz")
        self.a_end = QLineEdit(); self.a_end.setPlaceholderText("End MHz")
        self.a_mod = QLineEdit(); self.a_mod.setPlaceholderText("Modulation")
        self.a_cat = QLineEdit(); self.a_cat.setPlaceholderText("Category")
        for w in (self.a_name, self.a_start, self.a_end, self.a_mod, self.a_cat):
            af.addWidget(w)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._emit_add)
        af.addWidget(add_btn)
        del_btn = QPushButton("Delete Selected")
        del_btn.clicked.connect(self._emit_delete)
        af.addWidget(del_btn)
        root.addWidget(add_box)

        self.count = QLabel("0 known signals")
        root.addWidget(self.count)

    def set_signals(self, signals: List[dict]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(signals))
        for row, s in enumerate(signals):
            start = float(s.get("freq_start_hz", s.get("freq_hz", 0)) or 0) / 1e6
            end = float(s.get("freq_end_hz", start * 1e6) or start * 1e6) / 1e6
            vals = [s.get("id", ""), s.get("name", ""), f"{start:.4f}",
                    f"{end:.4f}", s.get("modulation", ""),
                    s.get("category", ""), s.get("description", "")]
            for col, v in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(str(v)))
        self.table.setSortingEnabled(True)
        self.count.setText(f"{len(signals)} known signals")

    def _emit_add(self) -> None:
        try:
            payload = {
                "name": self.a_name.text(),
                "freq_start_hz": float(self.a_start.text() or 0) * 1e6,
                "freq_end_hz": float(self.a_end.text() or self.a_start.text() or 0) * 1e6,
                "modulation": self.a_mod.text(),
                "category": self.a_cat.text(),
            }
        except ValueError:
            return
        if payload["name"]:
            self.add_requested.emit(payload)

    def _emit_delete(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item and item.text().isdigit():
            self.delete_requested.emit(int(item.text()))
