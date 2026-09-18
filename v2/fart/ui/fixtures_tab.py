"""Fixtures tab: add/duplicate/remove lights, and edit every field of the
currently selected one."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..config import ON_CONSOLE_LOSS_OPTIONS, ON_TRACKING_LOSS_OPTIONS, FixtureConfig
from ..gdtf import import_gdtf_channel_mapping, list_gdtf_modes
from .binding import bind_checkbox, bind_combo, bind_float, bind_int, bind_text


class FixturesTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.selected_index = 0

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        root.addLayout(left, stretch=0)
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        left.addWidget(self.list_widget)
        buttons = QHBoxLayout()
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._on_add)
        duplicate_button = QPushButton("Duplicate")
        duplicate_button.clicked.connect(self._on_duplicate)
        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self._on_remove)
        for b in (add_button, duplicate_button, remove_button):
            buttons.addWidget(b)
        left.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, stretch=1)
        self.editor_container = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_container)
        scroll.setWidget(self.editor_container)

        self._refresh_list()
        self._load_fixture(0)

    def _refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for fixture in self.main_window.settings.fixtures:
            self.list_widget.addItem(fixture.name)
        self.list_widget.blockSignals(False)
        if self.main_window.settings.fixtures:
            self.list_widget.setCurrentRow(min(self.selected_index, len(self.main_window.settings.fixtures) - 1))

    def _on_selection_changed(self, row):
        if row < 0:
            return
        self._load_fixture(row)

    def _on_add(self):
        self.main_window.settings.fixtures.append(FixtureConfig(name=f"Light {len(self.main_window.settings.fixtures) + 1}"))
        self.selected_index = len(self.main_window.settings.fixtures) - 1
        self._refresh_list()

    def _on_duplicate(self):
        fixtures = self.main_window.settings.fixtures
        source = fixtures[self.selected_index]
        from dataclasses import asdict
        copy = FixtureConfig(**asdict(source))
        copy.name = source.name + " copy"
        fixtures.append(copy)
        self.selected_index = len(fixtures) - 1
        self._refresh_list()

    def _on_remove(self):
        fixtures = self.main_window.settings.fixtures
        if len(fixtures) <= 1:
            QMessageBox.warning(self, "FART", "At least one light must remain configured.")
            return
        del fixtures[self.selected_index]
        self.selected_index = max(0, self.selected_index - 1)
        self._refresh_list()

    def _clear_editor(self):
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _load_fixture(self, index):
        fixtures = self.main_window.settings.fixtures
        if not 0 <= index < len(fixtures):
            return
        self.selected_index = index
        fixture = fixtures[index]
        self._clear_editor()

        identity = QGroupBox("Identity")
        form = QFormLayout(identity)
        name_edit = bind_text(QLineEdit(), fixture, "name")
        name_edit.editingFinished.connect(self._refresh_list)
        form.addRow("Name", name_edit)
        form.addRow("Enabled", bind_checkbox(QCheckBox(), fixture, "enabled"))
        form.addRow("PSN marker ID", bind_int(QLineEdit(), fixture, "marker_id", lo=0))
        form.addRow("Output universe", bind_int(QLineEdit(), fixture, "output_universe", lo=0))
        form.addRow("Optical centre X", bind_float(QLineEdit(), fixture, "x"))
        form.addRow("Optical centre Y", bind_float(QLineEdit(), fixture, "y"))
        form.addRow("Optical centre Z", bind_float(QLineEdit(), fixture, "z"))
        form.addRow("Intensity scale", bind_float(QLineEdit(), fixture, "intensity_scale", lo=0))
        self.editor_layout.addWidget(identity)

        mapping = QGroupBox("Physical angle mapping")
        form = QFormLayout(mapping)
        form.addRow("Pan-zero bearing", bind_float(QLineEdit(), fixture, "pan_zero_bearing"))
        form.addRow("Tilt-zero elevation", bind_float(QLineEdit(), fixture, "tilt_zero_elevation"))
        form.addRow("Pan direction", self._direction_combo(fixture, "pan_direction"))
        form.addRow("Tilt direction", self._direction_combo(fixture, "tilt_direction"))
        form.addRow("Pan trim offset", bind_float(QLineEdit(), fixture, "pan_offset"))
        form.addRow("Tilt trim offset", bind_float(QLineEdit(), fixture, "tilt_offset"))
        self.editor_layout.addWidget(mapping)

        limits = QGroupBox("Personality angle range")
        form = QFormLayout(limits)
        form.addRow("Pan minimum", bind_float(QLineEdit(), fixture, "pan_min"))
        form.addRow("Pan maximum", bind_float(QLineEdit(), fixture, "pan_max"))
        form.addRow("Tilt minimum", bind_float(QLineEdit(), fixture, "tilt_min"))
        form.addRow("Tilt maximum", bind_float(QLineEdit(), fixture, "tilt_max"))
        self.editor_layout.addWidget(limits)

        safety = QGroupBox("Limit safety")
        form = QFormLayout(safety)
        form.addRow(bind_checkbox(QCheckBox("Blackout on limit"), fixture, "blackout_on_limit"))
        form.addRow(bind_checkbox(QCheckBox("Set zoom to 100% on limit blackout"), fixture, "limit_blackout_zoom_100"))
        form.addRow(bind_checkbox(QCheckBox("Set iris to 100% on limit blackout"), fixture, "limit_blackout_iris_100"))
        self.editor_layout.addWidget(safety)

        channels = QGroupBox("DMX channels (absolute; 0 disables)")
        form = QFormLayout(channels)
        for label, attr in (
            ("Pan coarse", "pan_coarse"), ("Pan fine", "pan_fine"),
            ("Tilt coarse", "tilt_coarse"), ("Tilt fine", "tilt_fine"),
            ("Dimmer coarse", "dimmer"), ("Dimmer fine", "dimmer_fine"),
            ("Shutter", "shutter"), ("Shutter open value", "shutter_open"),
            ("Zoom coarse", "zoom"), ("Zoom fine", "zoom_fine"),
            ("Iris", "iris"), ("Iris 100% DMX", "iris_100_dmx"),
            ("Focus coarse", "focus"), ("Focus fine", "focus_fine"),
        ):
            form.addRow(label, bind_int(QLineEdit(), fixture, attr, lo=0, hi=512))
        gdtf_button = QPushButton("Import channels from GDTF…")
        gdtf_button.clicked.connect(lambda: self._on_import_gdtf(fixture))
        form.addRow(gdtf_button)
        self.editor_layout.addWidget(channels)

        direction_box = QGroupBox("Beam control direction")
        form = QFormLayout(direction_box)
        form.addRow(bind_checkbox(QCheckBox("Reverse zoom"), fixture, "zoom_reverse"))
        form.addRow(bind_checkbox(QCheckBox("Reverse iris"), fixture, "iris_reverse"))
        form.addRow(bind_checkbox(QCheckBox("Reverse focus"), fixture, "focus_reverse"))
        self.editor_layout.addWidget(direction_box)

        beam_model = QGroupBox("Auto zoom beam model")
        form = QFormLayout(beam_model)
        form.addRow("Beam angle at zoom 0%", bind_float(QLineEdit(), fixture, "zoom_angle_at_0", lo=0))
        form.addRow("Beam angle at zoom 100%", bind_float(QLineEdit(), fixture, "zoom_angle_at_100", lo=0))
        form.addRow("Iris physical at 0%", bind_float(QLineEdit(), fixture, "iris_physical_at_0", lo=0, hi=1))
        form.addRow("Iris physical at 100%", bind_float(QLineEdit(), fixture, "iris_physical_at_100", lo=0, hi=1))
        self.editor_layout.addWidget(beam_model)

        console = QGroupBox("Live console control (optional)")
        form = QFormLayout(console)
        form.addRow("Mode channel", bind_int(QLineEdit(), fixture, "console_mode_channel", lo=0, hi=512))
        form.addRow("Marker-select channel", bind_int(QLineEdit(), fixture, "console_marker_channel", lo=0, hi=512))
        form.addRow("On tracking loss", bind_combo(QComboBox(), fixture, "on_tracking_loss", ON_TRACKING_LOSS_OPTIONS))
        form.addRow("On console signal loss", bind_combo(QComboBox(), fixture, "on_console_loss", ON_CONSOLE_LOSS_OPTIONS))
        form.addRow("Marker-change blackout (s)", bind_float(QLineEdit(), fixture, "marker_change_blackout_s", lo=0))
        self.editor_layout.addWidget(console)

        self.editor_layout.addStretch(1)

    def _direction_combo(self, fixture, attr):
        combo = QComboBox()
        combo.addItems(["1", "-1"])
        combo.setCurrentText(str(getattr(fixture, attr)))
        combo.currentTextChanged.connect(lambda text: setattr(fixture, attr, int(text)))
        return combo

    def _on_import_gdtf(self, fixture):
        path, _filter = QFileDialog.getOpenFileName(self, "Select GDTF fixture file", "", "GDTF fixture (*.gdtf);;All files (*)")
        if not path:
            return
        try:
            modes = list_gdtf_modes(path)
            mode = modes[0]
            if len(modes) > 1:
                mode, ok = QInputDialog.getItem(self, "FART", "Select the GDTF DMX mode to import:", modes, 0, False)
                if not ok:
                    return
            start, ok = QInputDialog.getInt(self, "FART", "Fixture start DMX address?", 1, 1, 512)
            if not ok:
                return
            mapping, _modes, selected_mode = import_gdtf_channel_mapping(path, start, mode)
            for field, value in mapping.items():
                if hasattr(fixture, field):
                    setattr(fixture, field, value)
            self._load_fixture(self.selected_index)
            found = ", ".join(f"{k}={v}" for k, v in mapping.items())
            QMessageBox.information(self, "FART", f"Imported GDTF channel mapping (mode: {selected_mode}).\n\n"
                                                   f"Check these against the fixture manual.\n\n{found}")
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))
