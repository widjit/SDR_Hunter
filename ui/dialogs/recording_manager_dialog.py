"""Recording manager dialog.

Lists IQ recordings captured by :class:`core.recording_engine.IQRecorder`,
and lets the user preview their spectrum, play back demodulated audio, export
to WAV, annotate, run identification, and delete clips.

All heavy operations are guarded so the dialog never crashes the GUI even when
optional dependencies (``sounddevice``, ``sigmf``) are unavailable.
"""
from __future__ import annotations

import os
from typing import Any, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core import recording_engine
from core.recording_engine import load_recording


class RecordingManagerDialog(QDialog):
    """Browse, play, export, annotate, identify and delete IQ recordings."""

    def __init__(self, out_dir: str, classifier: Any = None,
                 matcher: Any = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recording Manager")
        self.resize(900, 600)
        self.out_dir = out_dir
        self.classifier = classifier
        self.matcher = matcher
        self._player = None
        self._records: List[dict] = []

        root = QHBoxLayout(self)

        # Left: table + buttons.
        left = QVBoxLayout()
        left.addWidget(QLabel("Recordings:"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Freq (MHz)", "Rate (Msps)", "Dur (s)", "Reason"])
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda *a: self._preview())
        left.addWidget(self.table)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Demod:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["fm", "am", "raw"])
        mode_row.addWidget(self.mode_combo)
        left.addLayout(mode_row)

        btn_row1 = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.stop_btn = QPushButton("■ Stop")
        self.export_btn = QPushButton("Export WAV…")
        for b in (self.play_btn, self.stop_btn, self.export_btn):
            btn_row1.addWidget(b)
        left.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.annotate_btn = QPushButton("Annotate…")
        self.identify_btn = QPushButton("Identify")
        self.delete_btn = QPushButton("Delete")
        self.refresh_btn = QPushButton("Refresh")
        for b in (self.annotate_btn, self.identify_btn, self.delete_btn,
                  self.refresh_btn):
            btn_row2.addWidget(b)
        left.addLayout(btn_row2)
        root.addLayout(left, stretch=3)

        # Right: preview + info.
        right = QVBoxLayout()
        self.preview_plot = pg.PlotWidget(title="Recording spectrum")
        self.preview_plot.setLabel("bottom", "Frequency", units="MHz")
        self.preview_plot.setLabel("left", "Power", units="dB")
        self.preview_curve = self.preview_plot.plot(pen=pg.mkPen("#33ffcc"))
        right.addWidget(self.preview_plot)
        self.info = QLabel("—")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.info)
        root.addLayout(right, stretch=4)

        self.play_btn.clicked.connect(self._play)
        self.stop_btn.clicked.connect(self._stop)
        self.export_btn.clicked.connect(self._export)
        self.annotate_btn.clicked.connect(self._annotate)
        self.identify_btn.clicked.connect(self._identify)
        self.delete_btn.clicked.connect(self._delete)
        self.refresh_btn.clicked.connect(self.refresh)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._records = recording_engine.list_recordings(self.out_dir)
        except Exception:  # noqa: BLE001
            self._records = []
        self.table.setRowCount(len(self._records))
        for row, rec in enumerate(self._records):
            freq = rec.get("frequency") or 0.0
            rate = rec.get("sample_rate") or 0.0
            dur = rec.get("duration_s") or 0.0
            cells = [
                rec.get("name", ""),
                f"{float(freq)/1e6:.4f}" if freq else "—",
                f"{float(rate)/1e6:.3f}" if rate else "—",
                f"{float(dur):.1f}" if dur else "—",
                rec.get("reason", ""),
            ]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(str(text)))

    def _current(self) -> Optional[dict]:
        row = self.table.currentRow()
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def _preview(self) -> None:
        rec = self._current()
        if not rec:
            return
        path = rec.get("data_path", "")
        if not path or not os.path.exists(path):
            return
        try:
            iq = load_recording(path)
        except Exception as exc:  # noqa: BLE001
            self.info.setText(f"Load error: {exc}")
            return
        if iq.size:
            n = min(iq.size, 8192)
            seg = iq[:n]
            win = np.hanning(len(seg))
            spec = np.fft.fftshift(np.fft.fft(seg * win))
            psd = 20.0 * np.log10(np.abs(spec) + 1e-9)
            rate = float(rec.get("sample_rate") or 1.0)
            freq0 = float(rec.get("frequency") or 0.0)
            axis = freq0 + np.linspace(-rate / 2, rate / 2, len(psd),
                                       endpoint=False)
            self.preview_curve.setData(axis / 1e6, psd)
        doc = recording_engine.read_meta(path)
        ann = doc.get("annotations", [])
        ident = doc.get("global", {}).get("sdrhunter:identification")
        lines = [
            f"<b>{rec.get('name','')}</b>",
            f"Frequency: {float(rec.get('frequency') or 0)/1e6:.4f} MHz",
            f"Sample rate: {float(rec.get('sample_rate') or 0)/1e6:.3f} Msps",
            f"Duration: {float(rec.get('duration_s') or 0):.2f} s",
            f"Reason: {rec.get('reason','')}",
            f"Samples: {iq.size}",
            f"Annotations: {len(ann)}",
        ]
        if ident:
            lines.append(f"Identification: {ident}")
        for a in ann[:10]:
            lines.append(f"• {a.get('core:label','')}: "
                         f"{a.get('core:comment','')}")
        self.info.setText("<br>".join(lines))

    def _play(self) -> None:
        rec = self._current()
        if not rec:
            return
        path = rec.get("data_path", "")
        try:
            from decoders.audio_player import AudioPlayer, HAVE_SOUNDDEVICE
            if not HAVE_SOUNDDEVICE:
                QMessageBox.information(
                    self, "Audio unavailable",
                    "The 'sounddevice' package is not installed; export to WAV "
                    "instead to listen to this recording.")
                return
            wav = recording_engine.export_wav(path, mode=self.mode_combo.currentText())
            import wave
            with wave.open(wav, "rb") as wf:
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            self._player = AudioPlayer(sample_rate=rate)
            self._player.start()
            self._player.play_once(audio)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Playback error", str(exc))

    def _stop(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001
                pass

    def _export(self) -> None:
        rec = self._current()
        if not rec:
            return
        path = rec.get("data_path", "")
        default = os.path.splitext(path)[0] + ".wav"
        out, _ = QFileDialog.getSaveFileName(self, "Export WAV", default,
                                             "WAV files (*.wav)")
        if not out:
            return
        try:
            recording_engine.export_wav(path, wav_path=out,
                                        mode=self.mode_combo.currentText())
            QMessageBox.information(self, "Exported", f"Saved {out}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export error", str(exc))

    def _annotate(self) -> None:
        rec = self._current()
        if not rec:
            return
        text, ok = QInputDialog.getText(self, "Annotate", "Comment:")
        if not ok:
            return
        label, _ = QInputDialog.getText(self, "Annotate", "Label (optional):")
        try:
            recording_engine.annotate_recording(
                rec.get("data_path", ""), comment=text, label=label or "")
            self._preview()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Annotate error", str(exc))

    def _identify(self) -> None:
        rec = self._current()
        if not rec:
            return
        try:
            result = recording_engine.identify_recording(
                rec.get("data_path", ""), classifier=self.classifier,
                matcher=self.matcher)
            QMessageBox.information(self, "Identification", str(result))
            self._preview()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Identify error", str(exc))

    def _delete(self) -> None:
        rec = self._current()
        if not rec:
            return
        if QMessageBox.question(
                self, "Delete", f"Delete recording '{rec.get('name','')}'?"
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            recording_engine.delete_recording(rec.get("data_path", ""))
        except Exception:  # noqa: BLE001
            pass
        self.refresh()
