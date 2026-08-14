"""ATAK / Cursor-on-Target (CoT) configuration dialog.

Reads and writes :class:`config.settings.ATAKSettings`. Lets the operator
configure the CoT transport (UDP multicast to the ATAK SA group, or TCP
unicast to a TAK server), the callsign and stale timeout, and which event
types are forwarded. A *Send Test Ping* button builds a real CoT event and
dispatches it through :class:`atak.cot_protocol.CoTSender` so the operator can
confirm connectivity before going live.

The dialog is fully defensive: every network operation is wrapped so a bad
address or unreachable server surfaces as a message box rather than a crash.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QRadioButton, QSpinBox,
                             QVBoxLayout, QWidget)

from atak.cot_protocol import CoTSender, SignalCoTEvent, new_uid


class ATAKConfigDialog(QDialog):
    """Configure ATAK/CoT output and send a test event."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ATAK / CoT Configuration")
        self.resize(460, 560)
        self.settings = settings
        atak = settings.atak

        root = QVBoxLayout(self)

        self.enable = QCheckBox("Enable ATAK / CoT output", self)
        self.enable.setChecked(bool(getattr(atak, "enabled", False)))
        root.addWidget(self.enable)

        # -- Transport selection ---------------------------------------
        transport_box = QGroupBox("Transport", self)
        tlay = QVBoxLayout(transport_box)
        self.rb_multicast = QRadioButton("UDP multicast (ATAK SA group)", self)
        self.rb_unicast = QRadioButton("TCP unicast (TAK server)", self)
        self._transport_group = QButtonGroup(self)
        self._transport_group.addButton(self.rb_multicast)
        self._transport_group.addButton(self.rb_unicast)
        if getattr(atak, "use_multicast", True):
            self.rb_multicast.setChecked(True)
        else:
            self.rb_unicast.setChecked(True)
        tlay.addWidget(self.rb_multicast)
        tlay.addWidget(self.rb_unicast)
        root.addWidget(transport_box)

        # -- Multicast settings ----------------------------------------
        mc_box = QGroupBox("Multicast", self)
        mc_form = QFormLayout(mc_box)
        self.mc_group = QLineEdit(str(getattr(atak, "multicast_group",
                                              "239.2.3.1")), self)
        self.mc_port = QSpinBox(self)
        self.mc_port.setRange(1, 65535)
        self.mc_port.setValue(int(getattr(atak, "multicast_port", 6969)))
        mc_form.addRow("Group:", self.mc_group)
        mc_form.addRow("Port:", self.mc_port)
        root.addWidget(mc_box)

        # -- Unicast settings ------------------------------------------
        uc_box = QGroupBox("Unicast (TCP)", self)
        uc_form = QFormLayout(uc_box)
        self.uc_host = QLineEdit(str(getattr(atak, "unicast_host", "")), self)
        self.uc_host.setPlaceholderText("takserver.example.com")
        self.uc_port = QSpinBox(self)
        self.uc_port.setRange(1, 65535)
        self.uc_port.setValue(int(getattr(atak, "unicast_port", 4242)))
        uc_form.addRow("Host:", self.uc_host)
        uc_form.addRow("Port:", self.uc_port)
        root.addWidget(uc_box)

        # -- Identity / event options ----------------------------------
        opt_box = QGroupBox("Options", self)
        opt_form = QFormLayout(opt_box)
        self.callsign = QLineEdit(str(getattr(atak, "callsign",
                                              "SDR-HUNTER")), self)
        self.stale = QSpinBox(self)
        self.stale.setRange(5, 3600)
        self.stale.setSuffix(" s")
        self.stale.setValue(int(getattr(atak, "stale_seconds", 120)))
        opt_form.addRow("Callsign:", self.callsign)
        opt_form.addRow("CoT stale:", self.stale)
        root.addWidget(opt_box)

        ev_box = QGroupBox("Forward event types", self)
        ev_lay = QVBoxLayout(ev_box)
        self.send_drones = QCheckBox("Drone tracks", self)
        self.send_drones.setChecked(bool(getattr(atak, "send_drones", True)))
        self.send_signals = QCheckBox("RF signal detections", self)
        self.send_signals.setChecked(bool(getattr(atak, "send_signals",
                                                   False)))
        self.send_anomalies = QCheckBox("Baseline anomalies", self)
        self.send_anomalies.setChecked(bool(getattr(atak, "send_anomalies",
                                                     False)))
        for cb in (self.send_drones, self.send_signals, self.send_anomalies):
            ev_lay.addWidget(cb)
        root.addWidget(ev_box)

        # -- Test ping + buttons ---------------------------------------
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Send Test Ping", self)
        self.test_btn.clicked.connect(self._send_test)
        test_row.addWidget(self.test_btn)
        test_row.addStretch(1)
        root.addLayout(test_row)

        self._toggle_transport()
        self.rb_multicast.toggled.connect(self._toggle_transport)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    def _toggle_transport(self) -> None:
        mc = self.rb_multicast.isChecked()
        self.mc_group.setEnabled(mc)
        self.mc_port.setEnabled(mc)
        self.uc_host.setEnabled(not mc)
        self.uc_port.setEnabled(not mc)

    def _build_sender(self) -> CoTSender:
        return CoTSender(
            multicast_group=self.mc_group.text().strip() or "239.2.3.1",
            multicast_port=int(self.mc_port.value()),
            unicast_host=self.uc_host.text().strip(),
            unicast_port=int(self.uc_port.value()),
            use_multicast=self.rb_multicast.isChecked(),
        )

    def _send_test(self) -> None:
        """Build a test CoT event and dispatch it through the sender."""
        sender = self._build_sender()
        # Use the operator/sensor location if the parent exposes one, else 0/0.
        lat, lon = 0.0, 0.0
        parent = self.parent()
        for attr in ("sensor_lat", "op_lat"):
            if parent is not None and hasattr(parent, attr):
                try:
                    lat = float(getattr(parent, attr))
                except (TypeError, ValueError):
                    pass
        ev = SignalCoTEvent(
            uid=new_uid("TEST"), lat=lat, lon=lon, freq_hz=0.0,
            label=f"{self.callsign.text().strip() or 'SDR-HUNTER'} TEST",
        )
        try:
            sender.send(ev.to_xml(stale_seconds=int(self.stale.value())))
        except OSError as exc:
            QMessageBox.critical(
                self, "Test Ping Failed",
                f"Could not send CoT event:\n{exc}")
            sender.close()
            return
        finally:
            sender.close()
        transport = ("multicast %s:%d" % (self.mc_group.text().strip(),
                                          self.mc_port.value())
                     if self.rb_multicast.isChecked()
                     else "unicast %s:%d" % (self.uc_host.text().strip(),
                                             self.uc_port.value()))
        QMessageBox.information(
            self, "Test Ping Sent",
            f"A test CoT event was dispatched via {transport}.\n\n"
            "Note: multicast delivery is best-effort (no error is raised if "
            "no ATAK client is listening).")

    def _accept(self) -> None:
        """Write the form values back to settings and persist."""
        atak = self.settings.atak
        atak.enabled = self.enable.isChecked()
        atak.use_multicast = self.rb_multicast.isChecked()
        atak.multicast_group = self.mc_group.text().strip() or "239.2.3.1"
        atak.multicast_port = int(self.mc_port.value())
        atak.unicast_host = self.uc_host.text().strip()
        atak.unicast_port = int(self.uc_port.value())
        atak.callsign = self.callsign.text().strip() or "SDR-HUNTER"
        atak.stale_seconds = int(self.stale.value())
        atak.send_drones = self.send_drones.isChecked()
        atak.send_signals = self.send_signals.isChecked()
        atak.send_anomalies = self.send_anomalies.isChecked()
        try:
            self.settings.save()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, "Save Failed",
                f"Settings could not be saved to disk:\n{exc}")
        self.accept()
