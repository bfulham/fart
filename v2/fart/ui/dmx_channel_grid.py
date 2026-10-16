"""A live 512-channel DMX value grid, laid out 32 columns x 16 rows
(channel 1 at top-left, increasing left-to-right then top-to-bottom) --
the same at-a-glance layout hardware DMX monitors like Artnetominator use,
so it's fast to scan for "is anything live/moving" without hunting for a
specific channel number. Hovering any cell names its channel outright
("Channel 37 = 128") so nobody has to count cells to find which one is
misbehaving.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

COLUMNS = 32
ROWS = 16
DARK_BG = QColor(35, 35, 38)
DARK_FG = QColor(110, 110, 115)


def channel_tooltip(channel, value):
    """1-based DMX channel number and its current value."""
    return f"Channel {channel} = {value}"


class DmxChannelGrid(QTableWidget):
    def __init__(self):
        super().__init__(ROWS, COLUMNS)
        self.horizontalHeader().setVisible(False)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for col in range(COLUMNS):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for row in range(ROWS):
            self.verticalHeader().setSectionResizeMode(row, QHeaderView.ResizeMode.Stretch)
        self._items = [[None] * COLUMNS for _ in range(ROWS)]
        for row in range(ROWS):
            for col in range(COLUMNS):
                item = QTableWidgetItem("0")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setBackground(DARK_BG)
                item.setForeground(DARK_FG)
                item.setToolTip(channel_tooltip(row * COLUMNS + col + 1, 0))
                self.setItem(row, col, item)
                self._items[row][col] = item
        self._last_values = [0] * (ROWS * COLUMNS)

    def set_frame(self, frame):
        """frame: a 512-byte DMX frame, or None/shorter to show all zero."""
        for i in range(ROWS * COLUMNS):
            value = frame[i] if frame is not None and i < len(frame) else 0
            if value == self._last_values[i]:
                continue
            self._last_values[i] = value
            item = self._items[i // COLUMNS][i % COLUMNS]
            item.setText(str(value))
            item.setToolTip(channel_tooltip(i + 1, value))
            if value:
                green = 60 + int((value / 255) * 160)
                item.setBackground(QColor(30, green, 60))
                item.setForeground(QColor(235, 255, 235))
            else:
                item.setBackground(DARK_BG)
                item.setForeground(DARK_FG)
