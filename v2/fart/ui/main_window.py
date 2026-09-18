"""Main window: owns Settings and the Runner, hosts the six tabs."""
from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from .. import APP_NAME
from ..config import load_settings, save_settings
from ..runner import Runner
from .calibration_tab import CalibrationTab
from .dmx_in_tab import DMXInTab
from .dmx_out_tab import DMXOutTab
from .fixtures_tab import FixturesTab
from .operator_tab import OperatorTab
from .psn_in_tab import PSNInTab

DEFAULT_CONFIG_FILE = Path.home() / "FART2.json"


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 840)

        self.config_path = config_path or DEFAULT_CONFIG_FILE
        self.settings = load_settings(self.config_path)
        self._log_queue: queue.Queue = queue.Queue()
        self.runner = Runner(log=self._log_queue.put)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.operator_tab = OperatorTab(self)
        self.psn_in_tab = PSNInTab(self)
        self.dmx_in_tab = DMXInTab(self)
        self.dmx_out_tab = DMXOutTab(self)
        self.fixtures_tab = FixturesTab(self)
        self.calibration_tab = CalibrationTab(self)

        self.tabs.addTab(self.operator_tab, "Operator")
        self.tabs.addTab(self.psn_in_tab, "PSN In")
        self.tabs.addTab(self.dmx_in_tab, "DMX In")
        self.tabs.addTab(self.dmx_out_tab, "DMX Out")
        self.tabs.addTab(self.fixtures_tab, "Fixtures")
        self.tabs.addTab(self.calibration_tab, "Calibration")

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._on_ui_tick)
        self.ui_timer.start(100)

    def _on_ui_tick(self):
        drained = False
        while True:
            try:
                message = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.operator_tab.append_log(message)
            drained = True
        self.operator_tab.refresh_status(self.runner.live)
        self.dmx_in_tab.refresh()
        self.dmx_out_tab.refresh()
        if drained and not self.runner.running and self._pending_crash():
            self._on_worker_crashed()

    def _pending_crash(self):
        # Runner marks itself not-running if its loop thread dies; only act
        # on that once, right after start() actually succeeded.
        return getattr(self, "_was_running", False) and not self.runner.running

    def _on_worker_crashed(self):
        self._was_running = False
        self.operator_tab.set_running(False)
        QMessageBox.critical(self, APP_NAME, "The output loop stopped unexpectedly. Check the log.")

    def start(self):
        try:
            self.runner.start(self.settings)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self._was_running = True
        self.operator_tab.set_running(True)

    def stop(self):
        self._was_running = False
        self.runner.stop()
        self.operator_tab.set_running(False)

    def do_save_settings(self):
        save_settings(self.config_path, self.settings)
        self._log_queue.put(f"Saved {self.config_path}")

    def closeEvent(self, event):
        if self.runner.running:
            self.stop()
        self.do_save_settings()
        super().closeEvent(event)
