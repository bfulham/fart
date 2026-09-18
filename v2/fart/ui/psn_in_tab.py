"""PSN In tab: OpenFollow PSN receive settings."""
from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .binding import bind_float, bind_int, bind_text


class PSNInTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        psn = main_window.settings.psn_in

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        form.addRow("PSN multicast group", bind_text(QLineEdit(), psn, "multicast"))
        form.addRow("PSN UDP port", bind_int(QLineEdit(), psn, "port", lo=1, hi=65535))
        form.addRow("PSN interface IP", bind_text(QLineEdit(), psn, "interface"))
        form.addRow("Default PSN marker", bind_int(QLineEdit(), psn, "default_marker_id", lo=0))
        form.addRow("Tracking timeout (s)", bind_float(QLineEdit(), psn, "timeout_s", lo=0.01))
        form.addRow("Smoothing (0-0.95)", bind_float(QLineEdit(), psn, "smoothing", lo=0.0, hi=0.95))
        form.addRow("Lead/lag (ms)", bind_float(QLineEdit(), psn, "lead_lag_ms", lo=-5000, hi=5000))

        self.discovered_label = QLabel("No scan run")
        layout.addWidget(self.discovered_label)
        scan_button = QPushButton("Auto-detect PSN trackers")
        scan_button.clicked.connect(self._on_scan_clicked)
        layout.addWidget(scan_button)
        layout.addStretch(1)

    def _on_scan_clicked(self):
        ids = sorted(self.main_window.runner.trackers.snapshot().keys())
        if ids:
            self.discovered_label.setText("Found tracker IDs: " + ", ".join(map(str, ids)))
        else:
            self.discovered_label.setText("No PSN trackers seen yet — start the app first, or wait a moment")
