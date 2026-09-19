"""Fixtures tab: a library of reusable Fixture Types (channel offsets +
physical properties, like patching a personality on a real console) plus
the fixture instances that reference them (position, calibration, and
three independent DMX patches: output, console shadow feed, console
mode/marker control)."""
from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..config import ON_CONSOLE_LOSS_OPTIONS, ON_TRACKING_LOSS_OPTIONS, FixtureConfig, FixtureType
from ..engine import fixture_type_for
from ..gdtf import import_gdtf_channel_mapping, list_gdtf_modes
from .binding import bind_checkbox, bind_combo, bind_float, bind_int, bind_text
from .gdtf_share_dialog import GDTFShareBrowseDialog


class FixturesTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.selected_index = 0
        self.selected_type_index = 0
        self.editor_mode = "fixture"

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        root.addLayout(left, stretch=0)

        left.addWidget(QLabel("Fixtures"))
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        # currentRowChanged only fires on an actual row-index change, not on
        # re-clicking an already-current row -- with only one fixture (or
        # re-clicking the same one after the editor switched away to show a
        # fixture type), that left the editor stuck on whatever was loaded
        # last. itemClicked fires on every click regardless.
        self.list_widget.itemClicked.connect(lambda item: self._load_fixture(self.list_widget.row(item)))
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

        left.addWidget(QLabel("Fixture Types"))
        self.type_list_widget = QListWidget()
        self.type_list_widget.currentRowChanged.connect(self._on_type_selection_changed)
        self.type_list_widget.itemClicked.connect(lambda item: self._load_type(self.type_list_widget.row(item)))
        left.addWidget(self.type_list_widget)
        type_buttons = QHBoxLayout()
        add_type_button = QPushButton("Add")
        add_type_button.clicked.connect(self._on_add_type)
        duplicate_type_button = QPushButton("Duplicate")
        duplicate_type_button.clicked.connect(self._on_duplicate_type)
        remove_type_button = QPushButton("Remove")
        remove_type_button.clicked.connect(self._on_remove_type)
        for b in (add_type_button, duplicate_type_button, remove_type_button):
            type_buttons.addWidget(b)
        left.addLayout(type_buttons)

        gdtf_type_buttons = QHBoxLayout()
        gdtf_button = QPushButton("Import from GDTF…")
        gdtf_button.clicked.connect(self._on_import_gdtf_clicked)
        gdtf_share_button = QPushButton("Browse GDTF Share…")
        gdtf_share_button.clicked.connect(self._on_browse_gdtf_share_clicked)
        for b in (gdtf_button, gdtf_share_button):
            gdtf_type_buttons.addWidget(b)
        left.addLayout(gdtf_type_buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, stretch=1)
        self.editor_container = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_container)
        scroll.setWidget(self.editor_container)

        self._refresh_list()
        self._refresh_type_list()
        self._load_fixture(0)

    # -- Fixture instances --------------------------------------------

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

    def _default_type_id(self):
        types = self.main_window.settings.fixture_types
        return types[0].id if types else ""

    def _on_add(self):
        settings = self.main_window.settings
        settings.fixtures.append(FixtureConfig(name=f"Light {len(settings.fixtures) + 1}", fixture_type_id=self._default_type_id()))
        self.selected_index = len(settings.fixtures) - 1
        self._refresh_list()

    def _on_duplicate(self):
        fixtures = self.main_window.settings.fixtures
        source = fixtures[self.selected_index]
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

    # -- Fixture types --------------------------------------------------

    def _refresh_type_list(self):
        self.type_list_widget.blockSignals(True)
        self.type_list_widget.clear()
        for fixture_type in self.main_window.settings.fixture_types:
            self.type_list_widget.addItem(fixture_type.name)
        self.type_list_widget.blockSignals(False)
        if self.main_window.settings.fixture_types:
            self.type_list_widget.setCurrentRow(min(self.selected_type_index, len(self.main_window.settings.fixture_types) - 1))

    def _on_type_selection_changed(self, row):
        if row < 0:
            return
        self._load_type(row)

    def _on_add_type(self):
        types = self.main_window.settings.fixture_types
        types.append(FixtureType(id=uuid.uuid4().hex, name=f"Fixture Type {len(types) + 1}"))
        self.selected_type_index = len(types) - 1
        self._refresh_type_list()

    def _on_duplicate_type(self):
        types = self.main_window.settings.fixture_types
        source = types[self.selected_type_index]
        copy = FixtureType(**asdict(source))
        copy.id = uuid.uuid4().hex
        copy.name = source.name + " copy"
        types.append(copy)
        self.selected_type_index = len(types) - 1
        self._refresh_type_list()

    def _on_remove_type(self):
        types = self.main_window.settings.fixture_types
        if len(types) <= 1:
            QMessageBox.warning(self, "FART", "At least one fixture type must remain.")
            return
        removed = types[self.selected_type_index]
        del types[self.selected_type_index]
        self.selected_type_index = max(0, self.selected_type_index - 1)
        fallback_id = types[0].id
        reassigned = [f.name for f in self.main_window.settings.fixtures if f.fixture_type_id == removed.id]
        for fixture in self.main_window.settings.fixtures:
            if fixture.fixture_type_id == removed.id:
                fixture.fixture_type_id = fallback_id
        self._refresh_type_list()
        if self.editor_mode == "fixture":
            self._load_fixture(self.selected_index)
        if reassigned:
            QMessageBox.information(
                self, "FART",
                f"'{removed.name}' was in use. Reassigned to '{types[0].name}': {', '.join(reassigned)}."
            )

    # -- Shared editor ----------------------------------------------------

    def _clear_editor(self):
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            widget = item.widget()
            if widget:
                # setParent(None) detaches (and hides) it immediately;
                # deleteLater() alone only schedules destruction for the
                # next event-loop pass, so within this same call stack --
                # e.g. list-selection signals firing during __init__,
                # followed by an explicit _load_fixture/_load_type call --
                # the old widgets stayed visible and overlapped the new
                # ones because nothing had removed them from view yet.
                widget.setParent(None)
                widget.deleteLater()

    def _load_fixture(self, index):
        fixtures = self.main_window.settings.fixtures
        if not 0 <= index < len(fixtures):
            return
        self.editor_mode = "fixture"
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
        form.addRow("Fixture Type", self._type_combo(fixture))
        form.addRow("Optical centre X", bind_float(QLineEdit(), fixture, "x"))
        form.addRow("Optical centre Y", bind_float(QLineEdit(), fixture, "y"))
        form.addRow("Optical centre Z", bind_float(QLineEdit(), fixture, "z"))
        self.editor_layout.addWidget(identity)

        fixture_type = fixture_type_for(self.main_window.settings, fixture)
        if fixture_type.gdtf_data:
            mode_box = QGroupBox("GDTF DMX mode")
            form = QFormLayout(mode_box)
            form.addRow("Mode", self._gdtf_mode_combo(fixture, fixture_type))
            self.editor_layout.addWidget(mode_box)

        mapping = QGroupBox("Physical angle mapping (calibration)")
        form = QFormLayout(mapping)
        form.addRow("Pan-zero bearing", bind_float(QLineEdit(), fixture, "pan_zero_bearing"))
        form.addRow("Tilt-zero elevation", bind_float(QLineEdit(), fixture, "tilt_zero_elevation"))
        form.addRow("Pan direction", self._direction_combo(fixture, "pan_direction"))
        form.addRow("Tilt direction", self._direction_combo(fixture, "tilt_direction"))
        form.addRow("Pan trim offset", bind_float(QLineEdit(), fixture, "pan_offset"))
        form.addRow("Tilt trim offset", bind_float(QLineEdit(), fixture, "tilt_offset"))
        self.editor_layout.addWidget(mapping)

        safety = QGroupBox("Limit safety")
        form = QFormLayout(safety)
        form.addRow(bind_checkbox(QCheckBox("Blackout on limit"), fixture, "blackout_on_limit"))
        form.addRow(bind_checkbox(QCheckBox("Set zoom to 100% on limit blackout"), fixture, "limit_blackout_zoom_100"))
        form.addRow(bind_checkbox(QCheckBox("Set iris to 100% on limit blackout"), fixture, "limit_blackout_iris_100"))
        self.editor_layout.addWidget(safety)

        output_patch = QGroupBox("DMX Out patch (FART's computed output to the real light)")
        form = QFormLayout(output_patch)
        form.addRow("Output universe", bind_int(QLineEdit(), fixture, "output_universe", lo=0))
        form.addRow("Output start address", bind_int(QLineEdit(), fixture, "output_start_address", lo=1, hi=512))
        self.editor_layout.addWidget(output_patch)

        shadow_patch = QGroupBox("Console shadow patch (optional -- live copy of this fixture's full "
                                  "real channel footprint, for passthrough of anything FART doesn't own)")
        form = QFormLayout(shadow_patch)
        form.addRow("Shadow universe (0 = none)", bind_int(QLineEdit(), fixture, "shadow_universe", lo=0))
        form.addRow("Shadow start address", bind_int(QLineEdit(), fixture, "shadow_start_address", lo=1, hi=512))
        self.editor_layout.addWidget(shadow_patch)

        console_patch = QGroupBox("Console mode/marker control (optional -- independent of the shadow patch, "
                                   "can be anywhere)")
        form = QFormLayout(console_patch)
        form.addRow("Console universe", bind_int(QLineEdit(), fixture, "console_universe", lo=0))
        form.addRow("Mode channel (0 = disabled)", bind_int(QLineEdit(), fixture, "console_mode_channel", lo=0, hi=512))
        form.addRow("Marker-select channel", bind_int(QLineEdit(), fixture, "console_marker_channel", lo=0, hi=512))
        form.addRow("On tracking loss", bind_combo(QComboBox(), fixture, "on_tracking_loss", ON_TRACKING_LOSS_OPTIONS))
        form.addRow("On console signal loss", bind_combo(QComboBox(), fixture, "on_console_loss", ON_CONSOLE_LOSS_OPTIONS))
        form.addRow("Marker-change blackout (s)", bind_float(QLineEdit(), fixture, "marker_change_blackout_s", lo=0))
        self.editor_layout.addWidget(console_patch)

        self.editor_layout.addStretch(1)

    def _type_combo(self, fixture):
        combo = QComboBox()
        types = self.main_window.settings.fixture_types
        for fixture_type in types:
            combo.addItem(fixture_type.name, fixture_type.id)
        current = combo.findData(fixture.fixture_type_id)
        combo.setCurrentIndex(max(0, current))

        def on_change(idx):
            fixture.fixture_type_id = combo.itemData(idx)
            # The new type may be GDTF-backed (or no longer be) -- reload
            # so the DMX mode row appears/disappears/repopulates to match.
            self._load_fixture(self.selected_index)

        combo.currentIndexChanged.connect(on_change)
        return combo

    def _gdtf_mode_combo(self, fixture, fixture_type):
        combo = QComboBox()
        try:
            modes = list_gdtf_modes(fixture_type.gdtf_data)
        except Exception:
            modes = []
        combo.addItems(modes)
        if fixture.gdtf_mode in modes:
            combo.setCurrentText(fixture.gdtf_mode)
        elif modes:
            # Nothing chosen yet (or a stale mode from a replaced file) --
            # default to the first mode rather than silently resolving to
            # the all-disabled baseline while the combo shows something else.
            fixture.gdtf_mode = modes[0]

        def on_change(text):
            fixture.gdtf_mode = text

        combo.currentTextChanged.connect(on_change)
        return combo

    def _direction_combo(self, fixture, attr):
        combo = QComboBox()
        combo.addItems(["1", "-1"])
        combo.setCurrentText(str(getattr(fixture, attr)))
        combo.currentTextChanged.connect(lambda text: setattr(fixture, attr, int(text)))
        return combo

    def _load_type(self, index):
        types = self.main_window.settings.fixture_types
        if not 0 <= index < len(types):
            return
        self.editor_mode = "type"
        self.selected_type_index = index
        fixture_type = types[index]
        self._clear_editor()
        is_gdtf = bool(fixture_type.gdtf_data)

        def add(form, widget, label=None):
            # Channel/physical fields on a GDTF-backed type aren't
            # meaningful to hand-edit -- the real values come from
            # whichever mode a given patched fixture selects (see
            # engine.effective_fixture_type) -- so they're shown, not hidden
            # (still useful context), but disabled.
            if label is not None:
                form.addRow(label, widget)
            else:
                form.addRow(widget)
            if is_gdtf:
                widget.setEnabled(False)
            return widget

        identity = QGroupBox("Identity")
        form = QFormLayout(identity)
        name_edit = bind_text(QLineEdit(), fixture_type, "name")
        name_edit.editingFinished.connect(self._refresh_type_and_fixture_lists)
        form.addRow("Name", name_edit)  # always editable, even for a GDTF-backed type
        add(form, bind_int(QLineEdit(), fixture_type, "footprint", lo=0, hi=512), "Footprint (0 = auto, from highest channel used)")
        add(form, bind_float(QLineEdit(), fixture_type, "intensity_scale", lo=0), "Intensity scale")
        self.editor_layout.addWidget(identity)

        if is_gdtf:
            source = QGroupBox("GDTF source")
            form = QFormLayout(source)
            origin_text = {"file": "Imported from a local GDTF file",
                           "share": "Imported from GDTF Share"}.get(fixture_type.gdtf_origin, "GDTF-backed")
            form.addRow(QLabel(origin_text))
            try:
                modes = list_gdtf_modes(fixture_type.gdtf_data)
            except Exception:
                modes = []
            modes_label = QLabel(", ".join(modes) if modes else "(file could not be read)")
            modes_label.setWordWrap(True)
            form.addRow("Available modes", modes_label)
            convert_button = QPushButton("Convert to custom copy…")
            convert_button.clicked.connect(lambda: self._on_convert_to_custom(fixture_type))
            form.addRow(convert_button)
            self.editor_layout.addWidget(source)
            note = QLabel("The fields below are read-only for a GDTF-backed type. Pick which mode a "
                          "fixture using this type runs on that fixture's own editor.")
            note.setWordWrap(True)
            self.editor_layout.addWidget(note)

        limits = QGroupBox("Physical angle range")
        form = QFormLayout(limits)
        add(form, bind_float(QLineEdit(), fixture_type, "pan_min"), "Pan minimum")
        add(form, bind_float(QLineEdit(), fixture_type, "pan_max"), "Pan maximum")
        add(form, bind_float(QLineEdit(), fixture_type, "tilt_min"), "Tilt minimum")
        add(form, bind_float(QLineEdit(), fixture_type, "tilt_max"), "Tilt maximum")
        self.editor_layout.addWidget(limits)

        channels = QGroupBox("Channel offsets within this fixture's own footprint (1-based; 0 disables)")
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
            add(form, bind_int(QLineEdit(), fixture_type, attr, lo=0, hi=512), label)
        self.editor_layout.addWidget(channels)

        direction_box = QGroupBox("Beam control direction")
        form = QFormLayout(direction_box)
        add(form, bind_checkbox(QCheckBox("Reverse zoom"), fixture_type, "zoom_reverse"))
        add(form, bind_checkbox(QCheckBox("Reverse iris"), fixture_type, "iris_reverse"))
        add(form, bind_checkbox(QCheckBox("Reverse focus"), fixture_type, "focus_reverse"))
        self.editor_layout.addWidget(direction_box)

        beam_model = QGroupBox("Auto zoom beam model")
        form = QFormLayout(beam_model)
        add(form, bind_float(QLineEdit(), fixture_type, "zoom_angle_at_0", lo=0), "Beam angle at zoom 0%")
        add(form, bind_float(QLineEdit(), fixture_type, "zoom_angle_at_100", lo=0), "Beam angle at zoom 100%")
        add(form, bind_float(QLineEdit(), fixture_type, "iris_physical_at_0", lo=0, hi=1), "Iris physical at 0%")
        add(form, bind_float(QLineEdit(), fixture_type, "iris_physical_at_100", lo=0, hi=1), "Iris physical at 100%")
        self.editor_layout.addWidget(beam_model)

        self.editor_layout.addStretch(1)

    def _on_convert_to_custom(self, fixture_type):
        try:
            modes = list_gdtf_modes(fixture_type.gdtf_data)
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))
            return
        mode = modes[0]
        if len(modes) > 1:
            mode, ok = QInputDialog.getItem(self, "FART", "Bake in values from which DMX mode?", modes, 0, False)
            if not ok:
                return
        try:
            mapping, _modes, selected_mode = import_gdtf_channel_mapping(fixture_type.gdtf_data, 1, mode)
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))
            return
        types = self.main_window.settings.fixture_types
        copy = FixtureType(id=uuid.uuid4().hex, name=f"{fixture_type.name} (custom copy)")
        for field, value in mapping.items():
            if hasattr(copy, field):
                setattr(copy, field, value)
        types.append(copy)
        self.selected_type_index = len(types) - 1
        self._refresh_type_list()
        QMessageBox.information(
            self, "FART",
            f"Created an editable copy of '{fixture_type.name}' using mode '{selected_mode}'. "
            "The original GDTF-backed type is unchanged.")

    def _refresh_type_and_fixture_lists(self):
        self._refresh_type_list()
        self._refresh_list()

    def _add_gdtf_type(self, name, gdtf_data, origin, share_rid=0):
        """Both GDTF import paths land here: each always creates a brand
        new Fixture Type rather than overwriting whatever happened to be
        selected in the list -- picking an existing type to overwrite by
        accident is an easy, hard-to-notice mistake; a fresh type is not.

        No mode is picked and no channels are extracted here -- the whole
        file is stored as-is (gdtf_data) and every mode it declares is
        derived on demand (see engine.effective_fixture_type), so nothing
        about the fixture is ever lost to a mode choice made at import
        time. Which mode a given patched fixture actually runs is chosen
        later, on that fixture's own instance editor.
        """
        types = self.main_window.settings.fixture_types
        modes = list_gdtf_modes(gdtf_data)
        fixture_type = FixtureType(
            id=uuid.uuid4().hex, name=name or f"Fixture Type {len(types) + 1}",
            gdtf_data=gdtf_data, gdtf_origin=origin, gdtf_share_rid=share_rid, gdtf_source_label=name,
        )
        types.append(fixture_type)
        self.selected_type_index = len(types) - 1
        self._refresh_type_list()
        QMessageBox.information(
            self, "FART",
            f"Added '{fixture_type.name}' with {len(modes)} DMX mode(s): {', '.join(modes)}.\n\n"
            "Pick which mode each patched fixture using this type should run on that fixture's own editor.")

    def _on_import_gdtf_clicked(self):
        path, _filter = QFileDialog.getOpenFileName(self, "Select GDTF fixture file", "", "GDTF fixture (*.gdtf);;All files (*)")
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            list_gdtf_modes(data)  # fail fast on a corrupt/unreadable file
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))
            return
        self._add_gdtf_type(Path(path).stem, data, origin="file")

    def _on_browse_gdtf_share_clicked(self):
        dialog = GDTFShareBrowseDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._add_gdtf_type(dialog.result_label, dialog.result_data, origin="share", share_rid=dialog.result_share_rid)
