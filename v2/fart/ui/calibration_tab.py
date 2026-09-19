"""Calibration tab: pick fixtures, open the wizard."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .calibration_wizard import CalibrationWizard


class CalibrationTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Select one or more lights, then open the calibration wizard. "
            "Keep the lamp/shutter closed and put the marker at a known point first."
        ))

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.list_widget)
        self._refresh_list()

        open_button = QPushButton("Open fixture calibration wizard")
        open_button.clicked.connect(self._on_open_wizard)
        layout.addWidget(open_button)
        layout.addStretch(1)

    def _refresh_list(self):
        # Preserve the current multi-selection by name across a refresh --
        # this is called right before opening the wizard (to pick up any
        # fixtures added/renamed elsewhere) as well as at startup, and
        # unconditionally resetting to row 0 here was silently collapsing
        # a multi-fixture selection down to just one fixture every time the
        # wizard was opened.
        previously_selected = {item.text() for item in self.list_widget.selectedItems()}
        self.list_widget.clear()
        for fixture in self.main_window.settings.fixtures:
            self.list_widget.addItem(fixture.name)
        if not self.main_window.settings.fixtures:
            return
        restored = False
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).text() in previously_selected:
                self.list_widget.item(row).setSelected(True)
                restored = True
        if not restored:
            self.list_widget.setCurrentRow(0)

    def _on_open_wizard(self):
        self._refresh_list()
        indices = [i.row() for i in self.list_widget.selectedIndexes()] or [0]
        if not self.main_window.settings.fixtures:
            QMessageBox.warning(self, "FART", "Add a light first.")
            return
        wizard = CalibrationWizard(self.main_window, indices)
        wizard.exec()
