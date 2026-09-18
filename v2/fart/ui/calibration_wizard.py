"""Multi-fixture calibration wizard. Drives selected fixtures' pan/tilt/
dimmer/zoom/iris directly from sliders (bypassing the tracking engine
entirely, same as v1), captures aimed points, and solves for each
fixture's real position and zero angles independently against the shared
point list.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
    QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
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
        self.level_slider = self._slider(0, 100, 15)
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
        self.targets = list(DEFAULT_TARGETS)
        self.output = None
        self.output_running = False

        self.setWindowTitle("FART calibration wizard")
        self.resize(1000, 700)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Select a known point, aim every selected fixture at it with the sliders below, "
            "capture, repeat for at least four points, then solve."
        ))

        body = QHBoxLayout()
        root.addLayout(body, stretch=1)

        left = QVBoxLayout()
        body.addLayout(left)
        self.target_list = QListWidget()
        for name, x, y, z in self.targets:
            self.target_list.addItem(f"{name}: X {x:g}, Y {y:g}, Z {z:g}")
        self.target_list.setCurrentRow(0)
        left.addWidget(self.target_list)

        right = QVBoxLayout()
        body.addLayout(right, stretch=1)
        self.rows = {}
        for index in self.fixture_indices:
            row = _FixtureRow(self.fixtures[index], self.fixture_types[index])
            self.rows[index] = row
            right.addWidget(row)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start calibration output")
        self.start_button.clicked.connect(self._on_start_output)
        self.stop_button = QPushButton("Stop output / blackout")
        self.stop_button.clicked.connect(self._on_stop_output)
        self.capture_button = QPushButton("Capture point for all fixtures")
        self.capture_button.clicked.connect(self._on_capture)
        self.solve_button = QPushButton("Solve and apply all")
        self.solve_button.clicked.connect(self._on_solve)
        for b in (self.start_button, self.stop_button, self.capture_button, self.solve_button):
            controls.addWidget(b)
        right.addLayout(controls)

        self.status_label = QLabel("Output stopped")
        right.addWidget(self.status_label)

        self.sample_table = QTableWidget(0, 4)
        self.sample_table.setHorizontalHeaderLabels(["Fixture", "Captured point", "Pan", "Tilt"])
        right.addWidget(self.sample_table, stretch=1)

        self.output_timer = QTimer(self)
        self.output_timer.timeout.connect(self._output_tick)
        self.output_timer.start(33)

    def _on_start_output(self):
        if self.main_window.runner.running:
            QMessageBox.critical(self, "FART", "Stop the main FART output before calibration output.")
            return
        try:
            settings = self.main_window.settings
            output_cls = OUTPUT_PLUGINS[settings.dmx_out.active]
            self.output = output_cls()
            self.output.start(getattr(settings.dmx_out, settings.dmx_out.active))
            self.output_running = True
            self.status_label.setText("Calibration output running")
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))

    def _on_stop_output(self):
        self.output_running = False
        if self.output:
            try:
                self.output.send(blank_frames_for_settings(self.main_window.settings))
                self.output.close()
            except Exception:
                pass
            self.output = None
        self.status_label.setText("Output stopped / blackout sent")

    def _output_tick(self):
        if self.output_running and self.output:
            try:
                frames = blank_frames_for_settings(self.main_window.settings)
                for index in self.fixture_indices:
                    fixture = self.fixtures[index]
                    resolved = resolve_fixture(fixture, self.fixture_types[index])
                    row = self.rows[index]
                    frame = frames.setdefault(int(fixture.output_universe), bytearray(512))
                    write_fixture_to_frame(frame, resolved, row.pan(), row.tilt(), row.level(), False,
                                            row.zoom(), row.iris(), self.main_window.runner.focus_value)
                self.output.send(frames)
            except Exception as exc:
                self.status_label.setText("Output error: " + str(exc))
                self.output_running = False

    def _on_capture(self):
        row_idx = self.target_list.currentRow()
        name, x, y, z = self.targets[row_idx]
        for index in self.fixture_indices:
            row = self.rows[index]
            sample = (x, y, z, row.pan(), row.tilt())
            self.samples[index].append(sample)
            table_row = self.sample_table.rowCount()
            self.sample_table.insertRow(table_row)
            self.sample_table.setItem(table_row, 0, QTableWidgetItem(self.fixtures[index].name))
            self.sample_table.setItem(table_row, 1, QTableWidgetItem(f"{name} ({x:g}, {y:g}, {z:g})"))
            self.sample_table.setItem(table_row, 2, QTableWidgetItem(f"{sample[3]:.3f}"))
            self.sample_table.setItem(table_row, 3, QTableWidgetItem(f"{sample[4]:.3f}"))
        counts = ", ".join(f"{self.fixtures[i].name}: {len(self.samples[i])}" for i in self.fixture_indices)
        self.status_label.setText(f"Captured. Samples — {counts}. Need at least 4 per fixture.")

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
            self.status_label.setText("Applied:\n" + "\n".join(messages))
            self.main_window.fixtures_tab._refresh_list()
            self.main_window.fixtures_tab._load_fixture(self.fixture_indices[0])
        except Exception as exc:
            QMessageBox.critical(self, "FART", str(exc))

    def closeEvent(self, event):
        self._on_stop_output()
        super().closeEvent(event)
