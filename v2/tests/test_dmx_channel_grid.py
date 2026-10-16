import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from fart.ui.dmx_channel_grid import COLUMNS, DmxChannelGrid, channel_tooltip

_app = QApplication.instance() or QApplication([])


class ChannelTooltipTests(unittest.TestCase):
    def setUp(self):
        self.grid = DmxChannelGrid()

    def tip(self, row, col):
        return self.grid.item(row, col).toolTip()

    def test_every_cell_names_its_own_channel_number(self):
        self.assertEqual(self.tip(0, 0), "Channel 1 = 0")
        self.assertEqual(self.tip(0, COLUMNS - 1), "Channel 32 = 0")
        self.assertEqual(self.tip(1, 0), "Channel 33 = 0", "row boundary: 32 -> 33")
        self.assertEqual(self.tip(1, 4), "Channel 37 = 0")
        self.assertEqual(self.tip(15, COLUMNS - 1), "Channel 512 = 0")

    def test_no_formula_tooltip_on_the_grid_itself(self):
        self.assertEqual(self.grid.toolTip(), "")

    def test_tooltip_tracks_the_live_value(self):
        frame = bytearray(512)
        frame[36] = 128  # channel 37
        self.grid.set_frame(frame)
        self.assertEqual(self.tip(1, 4), "Channel 37 = 128")
        self.assertEqual(self.tip(1, 5), "Channel 38 = 0", "neighbours are unaffected")
        frame[36] = 0
        self.grid.set_frame(frame)
        self.assertEqual(self.tip(1, 4), "Channel 37 = 0")

    def test_channel_tooltip_format(self):
        self.assertEqual(channel_tooltip(512, 255), "Channel 512 = 255")


if __name__ == "__main__":
    unittest.main()
