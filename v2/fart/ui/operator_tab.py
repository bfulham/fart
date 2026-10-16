"""Operator tab: live show control only (start/stop, DMX-in switch, dimmer
and beam modes, faders, light overview, log). No setup here -- that lives
in the other tabs.

Every fader that currently has no effect is greyed out (see
_update_enabled_states), so what is enabled is always what is in control.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QSlider, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..engine import dmx_in_universes_needed, fixture_has_shadow

DIMMER_MODE_ITEMS = [("Manual (fader)", "manual"), ("DMX in", "dmx_in")]
BEAM_MODE_ITEMS = [("Manual (faders)", "manual"), ("Auto size", "auto_size"), ("DMX passthrough", "dmx")]


def _fill_mode_combo(combo, items, current):
    for label, value in items:
        combo.addItem(label, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))


def _set_item_enabled(combo, value, enabled):
    index = combo.findData(value)
    if index >= 0:
        combo.model().item(index).setEnabled(enabled)


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
        self.start_button = QPushButton("START")
        self.start_button.clicked.connect(self._on_start_stop_clicked)
        control_form.addRow(self.start_button)
        self.use_dmx_in_checkbox = QCheckBox("Use DMX in")
        self.use_dmx_in_checkbox.setToolTip(
            "Unticked: FART-only control. Every light behaves as if its DMX-in addresses were 0 "
            "(auto mode, configured marker, manual dimmer, manual or auto beam).")
        self.use_dmx_in_checkbox.setChecked(settings.use_dmx_in)
        self.use_dmx_in_checkbox.toggled.connect(self._on_use_dmx_in_toggled)
        control_form.addRow(self.use_dmx_in_checkbox)
        left.addWidget(control_box)

        fader_box = QGroupBox("Dimmer")
        fader_form = QFormLayout()
        fader_layout = QVBoxLayout(fader_box)
        fader_layout.addLayout(fader_form)
        self.dimmer_mode_combo = QComboBox()
        _fill_mode_combo(self.dimmer_mode_combo, DIMMER_MODE_ITEMS, settings.dimmer_mode)
        self.dimmer_mode_combo.currentIndexChanged.connect(self._on_dimmer_mode_changed)
        fader_form.addRow("Dimmer mode", self.dimmer_mode_combo)
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
        self.beam_mode_combo = QComboBox()
        _fill_mode_combo(self.beam_mode_combo, BEAM_MODE_ITEMS, settings.beam_mode)
        self.beam_mode_combo.currentIndexChanged.connect(self._on_beam_mode_changed)
        beam_form.addRow("Beam mode", self.beam_mode_combo)
        self.zoom_slider = self._beam_slider(beam_form, "Zoom", main_window.runner.zoom_value, "zoom_value")
        self.iris_slider = self._beam_slider(beam_form, "Iris", main_window.runner.iris_value, "iris_value")
        self.focus_slider = self._beam_slider(beam_form, "Focus", main_window.runner.focus_value, "focus_value")
        self.spot_diameter_edit = QLineEdit(f"{settings.auto_beam_diameter_m:g}")
        self.spot_diameter_edit.editingFinished.connect(self._on_spot_diameter_changed)
        beam_form.addRow("Spot diameter (m)", self.spot_diameter_edit)
        left.addWidget(beam_box)
        left.addStretch(1)
        self._update_enabled_states()

        right = QVBoxLayout()
        root.addLayout(right, stretch=1)

        status_box = QGroupBox("System status")
        status_form = QFormLayout(status_box)
        self.status_labels = {}
        for key in ("PSN", "DMX in", "Fader", "Lights", "State"):
            label = QLabel("—")
            status_form.addRow(key + ":", label)
            self.status_labels[key] = label
        right.addWidget(status_box)

        self.overview_table = QTableWidget(0, 10)
        self.overview_table.setHorizontalHeaderLabels(
            ["Light", "Marker", "Marker XYZ", "Pan/Tilt", "Distance", "DMX In Mode", "DMX In Marker",
             "Dimmer", "Beam", "State"])
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

    def _on_use_dmx_in_toggled(self, checked):
        self.main_window.settings.use_dmx_in = checked
        self._update_enabled_states()

    def _on_dimmer_mode_changed(self, _index):
        self.main_window.settings.dimmer_mode = self.dimmer_mode_combo.currentData()
        self._update_enabled_states()

    def _on_beam_mode_changed(self, _index):
        self.main_window.settings.beam_mode = self.beam_mode_combo.currentData()
        self._update_enabled_states()

    def reset_dimmer_fader(self):
        """Start always begins dark: the manual dimmer fader returns to 0."""
        self.fader_slider.setValue(0)
        self.main_window.runner.fader.update(0.0)

    def _update_enabled_states(self):
        """Grey out every control that has no effect right now, so only what
        is actually in control can be touched. A DMX-in mode only takes
        effect on lights that have a shadow feed and while Use DMX in is
        ticked; lights without one fall back to the faders, so a fader stays
        live as long as any enabled light would still use it."""
        settings = self.main_window.settings
        use_dmx = settings.use_dmx_in
        lights = [f for f in settings.fixtures if f.enabled]
        fallback_exists = (not use_dmx) or any(not fixture_has_shadow(f) for f in lights) or not lights

        for combo, value in ((self.dimmer_mode_combo, "dmx_in"), (self.beam_mode_combo, "dmx")):
            _set_item_enabled(combo, value, use_dmx)

        dimmer_by_dmx = settings.dimmer_mode == "dmx_in" and use_dmx and not fallback_exists
        beam_dmx = settings.beam_mode == "dmx" and use_dmx and not fallback_exists
        beam_auto = settings.beam_mode == "auto_size"
        self.fader_slider.setEnabled(not dimmer_by_dmx)
        self.zoom_slider.setEnabled(not (beam_dmx or beam_auto))
        self.iris_slider.setEnabled(not (beam_dmx or beam_auto))
        self.focus_slider.setEnabled(not beam_dmx)
        self.spot_diameter_edit.setEnabled(beam_auto)

    def _on_fader_changed(self, value):
        fraction = value / 1000.0
        self.main_window.runner.fader.update(fraction)
        self.fader_label.setText(f"{fraction * 100:.1f}%")

    def _on_spot_diameter_changed(self):
        try:
            value = max(0.1, min(5.0, float(self.spot_diameter_edit.text())))
        except ValueError:
            value = self.main_window.settings.auto_beam_diameter_m
        self.main_window.settings.auto_beam_diameter_m = value
        self.spot_diameter_edit.setText(f"{value:g}")

    def set_running(self, running: bool):
        self.start_button.setText("RUNNING — click to stop" if running else "START")

    def append_log(self, message: str):
        self.log_view.appendPlainText(message)

    def refresh_status(self, live):
        self._update_enabled_states()
        self._refresh_dmx_in_status()
        if live is None:
            return
        lights = live.get("lights", [])
        stale_count = sum(bool(item.get("stale")) for item in lights)
        dmx_lost_count = sum(bool(item.get("dmx_lost")) for item in lights)
        self.status_labels["Fader"].setText(f"{live.get('fader', 0.0) * 100:.1f}%")
        self.status_labels["Lights"].setText(f"{len(lights)} enabled, {stale_count} lost")
        if stale_count:
            state = "TRACKING WARNINGS"
        elif dmx_lost_count:
            state = "DMX-IN LOST"
        else:
            state = "LIVE"
        self.status_labels["State"].setText(state)

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
                elif item.get("dmx_lost"):
                    state = "DMX-IN LOST"
                elif item.get("limit_blackout"):
                    state = "LIMIT BLACKOUT"
                elif item.get("blackout"):
                    state = "BLACKOUT"
                else:
                    state = "LIVE"
            dmx_mode = item.get("dmx_mode")
            dmx_marker = item.get("dmx_marker")
            values = [
                item.get("name", ""),
                str(item.get("marker_id", "")),
                f"{marker_xyz[0]:.2f}, {marker_xyz[1]:.2f}, {marker_xyz[2]:.2f}",
                angles,
                distance,
                dmx_mode.capitalize() if dmx_mode else "—",
                str(dmx_marker) if dmx_marker is not None else "—",
                item.get("dimmer_source", "—"),
                item.get("beam_source", "—"),
                state,
            ]
            for col, value in enumerate(values):
                self.overview_table.setItem(row, col, QTableWidgetItem(value))

    def _refresh_dmx_in_status(self):
        """Per-universe packet rate and last-packet age for every universe
        some fixture reads, so a slow or dead console feed is visible at a
        glance rather than only showing up as mysterious flicker."""
        runner = self.main_window.runner
        if not runner.running or runner.settings is None:
            self.status_labels["DMX in"].setText("—")
            return
        needed = sorted(dmx_in_universes_needed(runner.settings))
        if not needed:
            self.status_labels["DMX in"].setText("not used")
            return
        if not runner.settings.use_dmx_in:
            self.status_labels["DMX in"].setText("bypassed (Use DMX in is off)")
            return
        rates = runner.bus.rates()
        parts = []
        for universe in needed:
            if universe in rates:
                rate, age = rates[universe]
                parts.append(f"U{universe}: {rate:.1f} Hz, last {age:.1f}s ago")
            else:
                parts.append(f"U{universe}: no data")
        self.status_labels["DMX in"].setText("; ".join(parts))
