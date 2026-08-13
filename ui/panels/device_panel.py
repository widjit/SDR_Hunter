"""Device control dock panel.

Scans for SoapySDR devices (via DeviceManager, always falling back to the mock
device), lets the user assign a device to RX0/RX1 and configure per-channel
frequency, sample rate, gain, bandwidth, antenna, corrections and BladeRF
specific options. All hardware access is guarded — the panel works with the
mock device when no hardware is present.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSlider, QSpinBox,
                             QVBoxLayout, QWidget)
from PyQt6.QtCore import Qt

SAMPLE_RATES = [("0.25 MS/s", 250e3), ("1.024 MS/s", 1.024e6),
                ("2.048 MS/s", 2.048e6), ("2.4 MS/s", 2.4e6),
                ("5 MS/s", 5e6), ("10 MS/s", 10e6), ("20 MS/s", 20e6),
                ("40 MS/s", 40e6)]


class _ChannelConfig(QGroupBox):
    """Per-channel (RX0/RX1) configuration form."""

    changed = pyqtSignal(int, dict)  # channel, config dict

    def __init__(self, channel: int, title: str):
        super().__init__(title)
        self.channel = channel
        form = QFormLayout(self)

        self.freq = QDoubleSpinBox()
        self.freq.setRange(0.1, 6000.0)
        self.freq.setDecimals(4)
        self.freq.setValue(100.0)
        self.freq.setSuffix(" MHz")
        form.addRow("Center Freq", self.freq)

        self.rate = QComboBox()
        for label, _ in SAMPLE_RATES:
            self.rate.addItem(label)
        self.rate.setCurrentIndex(2)
        form.addRow("Sample Rate", self.rate)

        self.gain_mode = QComboBox()
        self.gain_mode.addItems(["Manual", "Auto (AGC)"])
        form.addRow("Gain Mode", self.gain_mode)

        gain_row = QHBoxLayout()
        self.gain = QSlider(Qt.Orientation.Horizontal)
        self.gain.setRange(0, 60)
        self.gain.setValue(30)
        self.gain_lbl = QLabel("30 dB")
        self.gain_lbl.setProperty("readout", "true")
        self.gain.valueChanged.connect(
            lambda v: self.gain_lbl.setText(f"{v} dB"))
        gain_row.addWidget(self.gain)
        gain_row.addWidget(self.gain_lbl)
        form.addRow("Gain", gain_row)

        self.bandwidth = QDoubleSpinBox()
        self.bandwidth.setRange(0.05, 56.0)
        self.bandwidth.setValue(2.0)
        self.bandwidth.setSuffix(" MHz")
        form.addRow("Bandwidth", self.bandwidth)

        self.antenna = QComboBox()
        self.antenna.addItems(["RX", "RX1", "RX2", "LNAL", "LNAH", "LNAW"])
        form.addRow("Antenna", self.antenna)

        self.ppm = QSpinBox()
        self.ppm.setRange(-200, 200)
        form.addRow("PPM Corr.", self.ppm)

        self.dc_offset = QCheckBox("DC offset correction")
        form.addRow(self.dc_offset)
        self.iq_balance = QCheckBox("IQ imbalance correction")
        form.addRow(self.iq_balance)

        for w in (self.freq, self.bandwidth):
            w.valueChanged.connect(self._emit)
        self.rate.currentIndexChanged.connect(self._emit)
        self.gain.valueChanged.connect(self._emit)

    def _emit(self, *_) -> None:
        self.changed.emit(self.channel, self.config())

    def config(self) -> dict:
        return {
            "channel": self.channel,
            "center_freq": self.freq.value() * 1e6,
            "sample_rate": SAMPLE_RATES[self.rate.currentIndex()][1],
            "gain_mode": self.gain_mode.currentText(),
            "gain_db": float(self.gain.value()),
            "bandwidth": self.bandwidth.value() * 1e6,
            "antenna": self.antenna.currentText(),
            "ppm": self.ppm.value(),
            "dc_offset": self.dc_offset.isChecked(),
            "iq_balance": self.iq_balance.isChecked(),
        }


class DevicePanelWidget(QWidget):
    """Device scan + assignment + per-channel configuration."""

    scan_requested = pyqtSignal()
    device_assigned = pyqtSignal(str, dict)   # "RX0"/"RX1", device dict
    channel_config_changed = pyqtSignal(int, dict)
    test_signal_toggled = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._devices: List[dict] = []

        root = QVBoxLayout(self)

        self.scan_btn = QPushButton("Scan for Devices")
        self.scan_btn.clicked.connect(self.scan_requested)
        root.addWidget(self.scan_btn)

        self.device_list = QListWidget()
        self.device_list.setMaximumHeight(110)
        root.addWidget(self.device_list)

        assign = QHBoxLayout()
        self.assign_rx0 = QPushButton("Assign → RX0")
        self.assign_rx1 = QPushButton("Assign → RX1")
        self.assign_rx0.clicked.connect(lambda: self._assign("RX0"))
        self.assign_rx1.clicked.connect(lambda: self._assign("RX1"))
        assign.addWidget(self.assign_rx0)
        assign.addWidget(self.assign_rx1)
        root.addLayout(assign)

        self.rx0_cfg = _ChannelConfig(0, "RX0 — Scanner")
        self.rx1_cfg = _ChannelConfig(1, "RX1 — Focus")
        self.rx0_cfg.changed.connect(self.channel_config_changed)
        self.rx1_cfg.changed.connect(self.channel_config_changed)
        root.addWidget(self.rx0_cfg)
        root.addWidget(self.rx1_cfg)

        bladerf = QGroupBox("BladeRF-specific")
        bl = QFormLayout(bladerf)
        self.mimo = QCheckBox("MIMO (2x2) mode")
        bl.addRow(self.mimo)
        self.bias_tee = QCheckBox("Bias-tee (antenna power)")
        bl.addRow(self.bias_tee)
        self.clock_src = QComboBox()
        self.clock_src.addItems(["Internal", "External 10 MHz", "Ref-in"])
        bl.addRow("Clock source", self.clock_src)
        root.addWidget(bladerf)

        self.test_signal = QCheckBox("Enable test signal generator (mock)")
        self.test_signal.toggled.connect(self.test_signal_toggled)
        root.addWidget(self.test_signal)
        root.addStretch(1)

    # ------------------------------------------------------------------
    def set_devices(self, devices: List[dict]) -> None:
        self._devices = devices
        self.device_list.clear()
        for d in devices:
            item = QListWidgetItem(
                f"{d.get('driver','?')} — {d.get('label','SDR')} "
                f"[{d.get('serial','')}]")
            self.device_list.addItem(item)
        if devices:
            self.device_list.setCurrentRow(0)

    def _assign(self, role: str) -> None:
        row = self.device_list.currentRow()
        if 0 <= row < len(self._devices):
            self.device_assigned.emit(role, self._devices[row])
