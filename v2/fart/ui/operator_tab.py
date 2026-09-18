"""Operator tab: live show control only (start/stop, arm, fader, beam,
light overview, log). No setup here -- that lives in the other tabs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSlider, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


class OperatorTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        settings = main_window.settings

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        root.addLayout(left, stretch=0)

        control_box = QGroupBox("Tracking and output")
        control_form = QFormLayout(control_box)
        self.start_button = QPushButton("START — DIMMER LOCKED")
        self.start_button.clicked.connect(self._on_start_stop_clicked)
        control_form.addRow(self.start_button)
        self.arm_checkbox = QCheckBox("Arm all light dimmers")
        self.arm_checkbox.toggled.connect(self._on_armed_toggled)
        control_form.addRow(self.arm_checkbox)
        left.addWidget(control_box)

        fader_box = QGroupBox("Manual fader")
        fader_layout = QVBoxLayout(fader_box)
        self.fader_slider = QSlider(Qt.Orientation.Horizontal)
        self.fader_slider.setRange(0, 1000)
        self.fader_slider.setValue(0)
        self.fader_slider.valueChanged.connect(self._on_fader_changed)
        self.fader_label = QLabel("0.0%")
        fader_layout.addWidget(self.fader_slider)
        fader_layout.addWidget(self.fader_label)
        left.addWidget(fader_box)

        beam_box = QGroupBox("Beam controls — shared normalized output")
        beam_form = QFormLayout(beam_box)
        self.zoom_slider = self._beam_slider(beam_form, "Zoom", main_window.runner.zoom_value, "zoom_value")
        self.iris_slider = self._beam_slider(beam_form, "Iris", main_window.runner.iris_value, "iris_value")
        self.focus_slider = self._beam_slider(beam_form, "Focus", main_window.runner.focus_value, "focus_value")
        self.zoom_mode_combo = QComboBox()
        self.zoom_mode_combo.addItems(["Manual", "Auto beam size"])
        self.zoom_mode_combo.setCurrentText(settings.zoom_mode)
        self.zoom_mode_combo.currentTextChanged.connect(self._on_zoom_mode_changed)
        beam_form.addRow("Zoom mode", self.zoom_mode_combo)
        self.spot_diameter_edit = QLineEdit(f"{settings.auto_beam_diameter_m:g}")
        self.spot_diameter_edit.editingFinished.connect(self._on_spot_diameter_changed)
        beam_form.addRow("Spot diameter (m)", self.spot_diameter_edit)
        left.addWidget(beam_box)
        left.addStretch(1)

        right = QVBoxLayout()
        root.addLayout(right, stretch=1)

        status_box = QGroupBox("System status")
        status_form = QFormLayout(status_box)
        self.status_labels = {}
        for key in ("PSN", "Fader", "Lights", "State"):
            label = QLabel("—")
            status_form.addRow(key + ":", label)
            self.status_labels[key] = label
        right.addWidget(status_box)

        self.overview_table = QTableWidget(0, 6)
        self.overview_table.setHorizontalHeaderLabels(["Light", "Marker", "Marker XYZ", "Pan/Tilt", "Distance", "State"])
        self.overview_table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.overview_table, stretch=1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        right.addWidget(self.log_view, stretch=1)

    def _beam_slider(self, form, label, initial, attr_name):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 1000)
        slider.setValue(int(initial * 1000))
        value_label = QLabel(f"{initial * 100:.1f}%")
        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(value_label)
        container = QWidget()
        container.setLayout(row)

        def on_change(value):
            fraction = value / 1000.0
            setattr(self.main_window.runner, attr_name, fraction)
            value_label.setText(f"{fraction * 100:.1f}%")

        slider.valueChanged.connect(on_change)
        form.addRow(label, container)
        return slider

    def _on_start_stop_clicked(self):
        if self.main_window.runner.running:
            self.main_window.stop()
        else:
            self.main_window.start()

    def _on_armed_toggled(self, checked):
        self.main_window.runner.armed = checked

    def _on_fader_changed(self, value):
        fraction = value / 1000.0
        self.main_window.runner.fader.update(fraction)
        self.fader_label.setText(f"{fraction * 100:.1f}%")

    def _on_zoom_mode_changed(self, text):
        self.main_window.settings.zoom_mode = text

    def _on_spot_diameter_changed(self):
        try:
            value = max(0.1, min(5.0, float(self.spot_diameter_edit.text())))
        except ValueError:
            value = self.main_window.settings.auto_beam_diameter_m
        self.main_window.settings.auto_beam_diameter_m = value
        self.spot_diameter_edit.setText(f"{value:g}")

    def set_running(self, running: bool):
        self.start_button.setText("RUNNING — click to stop" if running else "START — DIMMER LOCKED")

    def append_log(self, message: str):
        self.log_view.appendPlainText(message)

    def refresh_status(self, live):
        if live is None:
            return
        lights = live.get("lights", [])
        stale_count = sum(bool(item.get("stale")) for item in lights)
        self.status_labels["Fader"].setText(f"{live.get('fader', 0.0) * 100:.1f}%")
        self.status_labels["Lights"].setText(f"{len(lights)} enabled, {stale_count} lost")
        armed = self.main_window.runner.armed
        self.status_labels["State"].setText(
            "ALL DIMMERS LOCKED" if not armed else ("TRACKING WARNINGS" if stale_count else "LIVE")
        )

        self.overview_table.setRowCount(len(lights))
        for row, item in enumerate(lights):
            marker_xyz = item.get("marker_xyz", (0.0, 0.0, 0.0))
            if item.get("error"):
                angles = "—"
                distance = "—"
                state = item["error"]
            else:
                angles = f"{item.get('pan', 0.0):.1f}° / {item.get('tilt', 0.0):.1f}°"
                distance = f"{item.get('distance', 0.0):.2f} m"
                if item.get("stale"):
                    state = "TRACKING LOST"
                elif item.get("limit_blackout"):
                    state = "LIMIT BLACKOUT"
                elif item.get("blackout"):
                    state = "DIMMER LOCKED"
                else:
                    state = "LIVE"
            values = [
                item.get("name", ""),
                str(item.get("marker_id", "")),
                f"{marker_xyz[0]:.2f}, {marker_xyz[1]:.2f}, {marker_xyz[2]:.2f}",
                angles,
                distance,
                state,
            ]
            for col, value in enumerate(values):
                self.overview_table.setItem(row, col, QTableWidgetItem(value))
