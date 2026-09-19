"""Multi-fixture calibration wizard.

Guided flow: pick default or custom target points, then step through
every (point, fixture) pair one at a time -- only the current fixture is
lit (at whatever pan/tilt/beam its sliders say), every other fixture stays
blacked out, "Capture & Next" saves that fixture's aim at the current
point and advances to the next fixture, cycling through every fixture
before moving to the next point. Once every point has been captured for
every fixture, solve and apply per fixture against the shared point list.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QRadioButton, QSlider,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..calibration import solve_fixture_calibration
from ..engine import blank_frames_for_settings, fixture_type_for, resolve_fixture, write_fixture_to_frame
from ..plugins import OUTPUT_PLUGINS

DEFAULT_TARGETS = [
    ("Centre floor", 0.0, 0.0, 0.0),
    ("House right floor", 5.0, 0.0, 0.0),
    ("House left floor", -5.0, 0.0, 0.0),
    ("Upstage floor", 0.0, 5.0, 0.0),
    ("Downstage floor", 0.0, -5.0, 0.0),
    ("Centre at head height", 0.0, 0.0, 1.7),
]


class _FixtureRow(QWidget):
    def __init__(self, fixture, fixture_type):
        super().__init__()
        self.fixture = fixture
        self.fixture_type = fixture_type
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(fixture.name))

        pan_min, pan_max = fixture_type.pan_min, fixture_type.pan_max
        tilt_min, tilt_max = fixture_type.tilt_min, fixture_type.tilt_max
        self.pan_slider = self._slider(pan_min, pan_max, (pan_min + pan_max) / 2)
        self.tilt_slider = self._slider(tilt_min, tilt_max, (tilt_min + tilt_max) / 2)
        self.level_slider = self._slider(0, 100, 100)
        self.zoom_slider = self._slider(0, 100, 50)
        self.iris_slider = self._slider(0, 100, 100)
        for label, slider in (("Pan", self.pan_slider), ("Tilt", self.tilt_slider),
                               ("Dimmer", self.level_slider), ("Zoom", self.zoom_slider), ("Iris", self.iris_slider)):
            layout.addWidget(QLabel(label))
            layout.addWidget(slider)

    def _slider(self, lo, hi, initial):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(lo * 100), int(hi * 100))
        slider.setValue(int(initial * 100))
        slider.setMinimumWidth(120)
        return slider

    def pan(self):
        return self.pan_slider.value() / 100.0

    def tilt(self):
        return self.tilt_slider.value() / 100.0

    def level(self):
        return self.level_slider.value() / 100.0 / 100.0

    def zoom(self):
        return self.zoom_slider.value() / 100.0 / 100.0

    def iris(self):
        return self.iris_slider.value() / 100.0 / 100.0


class CalibrationWizard(QDialog):
    def __init__(self, main_window, fixture_indices):
        super().__init__(main_window)
        self.main_window = main_window
        self.fixture_indices = list(fixture_indices)
        self.fixtures = {i: main_window.settings.fixtures[i] for i in self.fixture_indices}
        self.fixture_types = {i: fixture_type_for(main_window.settings, self.fixtures[i]) for i in self.fixture_indices}
        self.samples = {i: [] for i in self.fixture_indices}
        self.output = None
        self.output_running = False

        self.points = []
        self._custom_points = []
        self.point_index = 0
        self.fixture_pos = 0

        self.setWindowTitle("FART calibration wizard")
        self.resize(1000, 700)

        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        self.rows = {
            index: _FixtureRow(self.fixtures[index], self.fixture_types[index])
            for index in self.fixture_indices
        }
        self.fixture_stack = QStackedWidget()
        for row in self.rows.values():
            self.fixture_stack.addWidget(row)

        self.setup_page = self._build_setup_page()
        self.guided_page = self._build_guided_page()
        self.done_page = self._build_done_page()
        self.stack.addWidget(self.setup_page)
        self.stack.addWidget(self.guided_page)
        self.stack.addWidget(self.done_page)
        self.stack.setCurrentWidget(self.setup_page)

        self.output_timer = QTimer(self)
        self.output_timer.timeout.connect(self._output_tick)
        self.output_timer.start(33)

    # -- Setup page: default vs custom points ---------------------------

    def _build_setup_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        names = ", ".join(f.name for f in self.fixtures.values())
        layout.addWidget(QLabel(f"Calibrating: {names}"))
        layout.addWidget(QLabel(
            "Keep each fixture's lamp/shutter closed until it's this wizard's turn to light it, "
            "and know where your known points are in the room before starting."
        ))

        choice_box = QGroupBox("Which points do you want to calibrate against?")
        choice_layout = QVBoxLayout(choice_box)
        self.default_radio = QRadioButton(f"Default points ({len(DEFAULT_TARGETS)} standard positions)")
        self.custom_radio = QRadioButton("Custom points (enter below)")
        self.default_radio.setChecked(True)
        group = QButtonGroup(page)
        group.addButton(self.default_radio)
        group.addButton(self.custom_radio)
        choice_layout.addWidget(self.default_radio)
        choice_layout.addWidget(self.custom_radio)
        layout.addWidget(choice_box)

        self.custom_points_box = QGroupBox("Custom points")
        self.custom_points_box.setEnabled(False)
        self.custom_radio.toggled.connect(self.custom_points_box.setEnabled)
        custom_layout = QVBoxLayout(self.custom_points_box)
        self.custom_point_list = QListWidget()
        custom_layout.addWidget(self.custom_point_list)

        entry_form = QFormLayout()
        self.setup_x_edit = QLineEdit("0")
        self.setup_y_edit = QLineEdit("0")
        self.setup_z_edit = QLineEdit("0")
        entry_form.addRow("X", self.setup_x_edit)
        entry_form.addRow("Y", self.setup_y_edit)
        entry_form.addRow("Z", self.setup_z_edit)
        custom_layout.addLayout(entry_form)

        custom_buttons = QHBoxLayout()
        add_point_button = QPushButton("Add point")
        add_point_button.clicked.connect(self._on_add_setup_point)
        remove_point_button = QPushButton("Remove selected")
        remove_point_button.clicked.connect(self._on_remove_setup_point)
        custom_buttons.addWidget(add_point_button)
        custom_buttons.addWidget(remove_point_button)
        custom_layout.addLayout(custom_buttons)
        layout.addWidget(self.custom_points_box)

        layout.addStretch(1)
        start_button = QPushButton("Start calibration →")
        start_button.clicked.connect(self._on_start_guided)
        layout.addWidget(start_button)
        return page

    def _on_add_setup_point(self):
        try:
            x = float(self.setup_x_edit.text())
            y = float(self.setup_y_edit.text())
            z = float(self.setup_z_edit.text())
        except ValueError as exc:
            QMessageBox.critical(self, "FART", f"X/Y/Z must be numbers: {exc}")
            return
        self._custom_points.append((f"Point {len(self._custom_points) + 1}", x, y, z))
        self.custom_point_list.addItem(f"Point {len(self._custom_points)}: X {x:g}, Y {y:g}, Z {z:g}")

    def _on_remove_setup_point(self):
        row = self.custom_point_list.currentRow()
        if row < 0:
            return
        del self._custom_points[row]
        self.custom_point_list.takeItem(row)

    def _on_start_guided(self):
        if self.custom_radio.isChecked():
            if not self._custom_points:
                QMessageBox.critical(self, "FART", "Add at least one custom point first.")
                return
            self.points = list(self._custom_points)
        else:
            self.points = list(DEFAULT_TARGETS)
        self.point_index = 0
        self.fixture_pos = 0
        self._start_output_internal()
        if self.output_running:
            self._enter_guided_step()
            self.stack.setCurrentWidget(self.guided_page)

    # -- Guided page: one fixture, one point, at a time ------------------

    def _build_guided_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.progress_label = QLabel()
        self.progress_label.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(self.progress_label)
        self.point_label = QLabel()
        layout.addWidget(self.point_label)

        layout.addWidget(self.fixture_stack)

        nav_row = QHBoxLayout()
        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self._on_guided_back)
        self.next_button = QPushButton("Capture && Next →")
        self.next_button.clicked.connect(self._on_guided_next)
        cancel_button = QPushButton("Cancel / blackout")
        cancel_button.clicked.connect(self._on_cancel_guided)
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.next_button)
        nav_row.addWidget(cancel_button)
        layout.addLayout(nav_row)

        self.guided_status_label = QLabel("")
        layout.addWidget(self.guided_status_label)

        self.sample_table = QTableWidget(0, 4)
        self.sample_table.setHorizontalHeaderLabels(["Fixture", "Captured point", "Pan", "Tilt"])
        layout.addWidget(self.sample_table, stretch=1)
        return page

    def _current_fixture_index(self):
        return self.fixture_indices[self.fixture_pos]

    def _enter_guided_step(self):
        name, x, y, z = self.points[self.point_index]
        fixture_index = self._current_fixture_index()
        self.progress_label.setText(
            f"Point {self.point_index + 1} of {len(self.points)}  ·  "
            f"Fixture {self.fixture_pos + 1} of {len(self.fixture_indices)}: {self.fixtures[fixture_index].name}"
        )
        self.point_label.setText(f"Aim this fixture at: {name} — X {x:g}, Y {y:g}, Z {z:g}")
        self.fixture_stack.setCurrentWidget(self.rows[fixture_index])
        self.back_button.setEnabled(not (self.point_index == 0 and self.fixture_pos == 0))
        counts = ", ".join(f"{self.fixtures[i].name}: {len(self.samples[i])}" for i in self.fixture_indices)
        self.guided_status_label.setText(f"Captured so far — {counts}")

    def _on_guided_next(self):
        name, x, y, z = self.points[self.point_index]
        fixture_index = self._current_fixture_index()
        row = self.rows[fixture_index]
        sample = (x, y, z, row.pan(), row.tilt())
        self.samples[fixture_index].append(sample)
        table_row = self.sample_table.rowCount()
        self.sample_table.insertRow(table_row)
        self.sample_table.setItem(table_row, 0, QTableWidgetItem(self.fixtures[fixture_index].name))
        self.sample_table.setItem(table_row, 1, QTableWidgetItem(f"{name} ({x:g}, {y:g}, {z:g})"))
        self.sample_table.setItem(table_row, 2, QTableWidgetItem(f"{sample[3]:.3f}"))
        self.sample_table.setItem(table_row, 3, QTableWidgetItem(f"{sample[4]:.3f}"))

        self.fixture_pos += 1
        if self.fixture_pos >= len(self.fixture_indices):
            self.fixture_pos = 0
            self.point_index += 1
        if self.point_index >= len(self.points):
            self._finish_guided()
            return
        self._enter_guided_step()

    def _on_guided_back(self):
        self.fixture_pos -= 1
        if self.fixture_pos < 0:
            if self.point_index == 0:
                self.fixture_pos = 0
                return
            self.point_index -= 1
            self.fixture_pos = len(self.fixture_indices) - 1
        # Undo the capture made for this step, if any, so re-capturing on
        # Next doesn't leave a duplicate sample behind.
        fixture_index = self._current_fixture_index()
        if self.samples[fixture_index]:
            self.samples[fixture_index].pop()
            last_row = self.sample_table.rowCount() - 1
            if last_row >= 0:
                self.sample_table.removeRow(last_row)
        self._enter_guided_step()

    def _on_cancel_guided(self):
        self._stop_output_internal()
        self.stack.setCurrentWidget(self.setup_page)

    def _finish_guided(self):
        self._stop_output_internal()
        self._populate_done_page()
        self.stack.setCurrentWidget(self.done_page)

    # -- Done page: solve and apply --------------------------------------

    def _build_done_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("All points captured for every fixture."))
        self.done_summary_table = QTableWidget(0, 2)
        self.done_summary_table.setHorizontalHeaderLabels(["Fixture", "Samples captured"])
        layout.addWidget(self.done_summary_table)

        solve_button = QPushButton("Solve and apply all")
        solve_button.clicked.connect(self._on_solve)
        layout.addWidget(solve_button)
        self.done_status_label = QLabel("")
        self.done_status_label.setWordWrap(True)
        layout.addWidget(self.done_status_label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        layout.addStretch(1)
        return page

    def _populate_done_page(self):
        self.done_summary_table.setRowCount(len(self.fixture_indices))
        for row, index in enumerate(self.fixture_indices):
            self.done_summary_table.setItem(row, 0, QTableWidgetItem(self.fixtures[index].name))
            self.done_summary_table.setItem(row, 1, QTableWidgetItem(str(len(self.samples[index]))))

    def _on_solve(self):
        messages = []
        try:
            for index in self.fixture_indices:
                fixture_type = self.fixture_types[index]
                solved, rms = solve_fixture_calibration(
                    self.fixtures[index], fixture_type.pan_min, fixture_type.pan_max, self.samples[index])
                self.main_window.settings.fixtures[index] = solved
                messages.append(
                    f"{solved.name}: XYZ=({solved.x:.3f}, {solved.y:.3f}, {solved.z:.3f}), "
                    f"pan zero={solved.pan_zero_bearing:.3f}, tilt zero={solved.tilt_zero_elevation:.3f}, "
                    f"fit={rms:.3f} m"
                )
            self.done_status_label.setText("Applied:\n" + "\n".join(messages))
            self.main_window.fixtures_tab._refresh_list()
            self.main_window.fixtures_tab._load_fixture(self.fixture_indices[0])
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))

    # -- Output --------------------------------------------------------

    def _start_output_internal(self):
        if self.output_running:
            return
        if self.main_window.runner.running:
            QMessageBox.critical(self, "FART", "Stop the main FART output before calibration output.")
            return
        try:
            settings = self.main_window.settings
            output_cls = OUTPUT_PLUGINS[settings.dmx_out.active]
            self.output = output_cls()
            self.output.start(getattr(settings.dmx_out, settings.dmx_out.active))
            self.output_running = True
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))

    def _stop_output_internal(self):
        self.output_running = False
        if self.output:
            try:
                self.output.send(blank_frames_for_settings(self.main_window.settings))
                self.output.close()
            except Exception:
                pass
            self.output = None

    def _output_tick(self):
        if not (self.output_running and self.output):
            return
        try:
            frames = blank_frames_for_settings(self.main_window.settings)
            # Only the fixture whose turn it currently is gets written --
            # every other fixture (including ones already done, or not
            # reached yet) stays at the blank/blacked-out frame above, even
            # when it shares a universe with the active fixture.
            if self.stack.currentWidget() is self.guided_page and self.points:
                fixture_index = self._current_fixture_index()
                fixture = self.fixtures[fixture_index]
                resolved = resolve_fixture(fixture, self.fixture_types[fixture_index])
                row = self.rows[fixture_index]
                frame = frames.setdefault(int(fixture.output_universe), bytearray(512))
                write_fixture_to_frame(frame, resolved, row.pan(), row.tilt(), row.level(), False,
                                        row.zoom(), row.iris(), self.main_window.runner.focus_value)
            self.output.send(frames)
        except Exception as exc:
            self.guided_status_label.setText("Output error: " + str(exc))
            self.output_running = False

    def closeEvent(self, event):
        self._stop_output_internal()
        super().closeEvent(event)
