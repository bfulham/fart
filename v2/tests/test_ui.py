"""Real UI tests: QTest synthesizes actual clicks/typing into actual Qt
widgets, entirely in-process (QT_QPA_PLATFORM=offscreen) -- unlike v1's
Tkinter UI, which needed OS-level Accessibility permission to automate and
so was never actually click-tested this session.
"""
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fart.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def psn_chunk(chunk_id, payload, sub=False):
    raw = (chunk_id & 0xFFFF) | ((len(payload) & 0x7FFF) << 16)
    if sub:
        raw |= 0x80000000
    return struct.pack("<I", raw) + payload


def build_psn_packet(marker_id, x, y, z):
    pos = psn_chunk(0x0000, struct.pack("<fff", x, y, z), sub=True)
    tracker = psn_chunk(marker_id, pos, sub=True)
    tracker_list = psn_chunk(0x0001, tracker, sub=True)
    return psn_chunk(0x6755, tracker_list, sub=True)


class WindowTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self._tmp.name) / "FART2.json"
        self.window = MainWindow(config_path=self.config_path)
        # Real layout/geometry matters for QTest.mouseClick's default
        # widget.rect().center() to actually land on a widget's hit region
        # (checkboxes/radio buttons in particular) -- without show(), some
        # widgets have degenerate 0x0 geometry and clicks silently miss.
        self.window.show()
        QTest.qWait(50)

    def tearDown(self):
        if self.window.runner.running:
            self.window.runner.stop()
        # Stop the UI timer explicitly and schedule real deletion: closing a
        # QMainWindow alone doesn't destroy it or its QTimer, so without
        # this, a previous test's window keeps firing its 100ms tick in the
        # background (touching now-half-torn-down widgets) while later
        # tests run, which was intermittently hanging the offscreen
        # platform rather than just being harmlessly wasteful.
        self.window.ui_timer.stop()
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(20)
        self._tmp.cleanup()


class BasicStructureTests(WindowTestCase):
    def test_all_tabs_present(self):
        labels = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())]
        self.assertEqual(labels, ["Operator", "PSN In", "DMX In", "DMX Out", "Fixtures", "Calibration"])


class FixturesTabTests(WindowTestCase):
    def test_add_button_click_adds_a_fixture(self):
        tab = self.window.fixtures_tab
        from PySide6.QtWidgets import QPushButton
        add_button = next(b for b in tab.findChildren(QPushButton) if b.text() == "Add")
        before = len(self.window.settings.fixtures)
        QTest.mouseClick(add_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.window.settings.fixtures), before + 1)
        self.assertEqual(tab.list_widget.count(), before + 1)

    def test_add_and_remove_via_direct_handlers(self):
        # Also exercise duplicate/remove directly (same code path a real
        # click on those buttons runs), confirming the real Qt list widget
        # (not a mock) reflects each change.
        tab = self.window.fixtures_tab
        before = len(self.window.settings.fixtures)
        tab._on_add()
        self.assertEqual(len(self.window.settings.fixtures), before + 1)
        self.assertEqual(tab.list_widget.count(), before + 1)

        tab._on_duplicate()
        self.assertEqual(len(self.window.settings.fixtures), before + 2)
        self.assertTrue(self.window.settings.fixtures[-1].name.endswith("copy"))

        while len(self.window.settings.fixtures) > 1:
            tab._on_remove()
        self.assertEqual(len(self.window.settings.fixtures), 1)

    def test_editing_name_field_updates_settings_and_list(self):
        tab = self.window.fixtures_tab
        tab._load_fixture(0)
        name_edit = tab.editor_container.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)[0]
        name_edit.clear()
        QTest.keyClicks(name_edit, "Spot 1")
        QTest.keyClick(name_edit, Qt.Key.Key_Return)
        self.assertEqual(self.window.settings.fixtures[0].name, "Spot 1")
        self.assertEqual(tab.list_widget.item(0).text(), "Spot 1")


class DMXInTabTests(WindowTestCase):
    def test_switching_protocol_preserves_both_universes(self):
        tab = self.window.dmx_in_tab
        from PySide6.QtWidgets import QLineEdit

        # QTabWidget hides inactive pages, which leaves their widgets
        # without real geometry until switched to -- keyboard events still
        # reach a widget by direct focus regardless, but a mouse click needs
        # real hit-testable geometry, so switch to this tab first (which is
        # also just what a real user would do).
        self.window.tabs.setCurrentWidget(tab)
        QTest.qWait(20)

        # Set Art-Net universe while it's active.
        artnet_edit = tab.stack.widget(0).findChildren(QLineEdit)[0]
        artnet_edit.clear()
        QTest.keyClicks(artnet_edit, "7")
        QTest.keyClick(artnet_edit, Qt.Key.Key_Return)
        self.assertEqual(self.window.settings.dmx_in.artnet.universe, 7)

        # Switch to sACN via a real click on the radio button. The layout
        # stretches these radio buttons to the tab's full width, but their
        # actual clickable hit-region is only their natural content size
        # (indicator + label) on the left -- clicking the default centre
        # (deep in empty stretch space) silently misses, same as a real
        # user would miss clicking on blank space next to a radio button.
        QTest.mouseClick(tab.sacn_radio, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        self.assertEqual(self.window.settings.dmx_in.active, "sacn")
        self.assertEqual(tab.stack.currentIndex(), 1)

        sacn_edit = tab.stack.widget(1).findChildren(QLineEdit)[0]
        sacn_edit.clear()
        QTest.keyClicks(sacn_edit, "9")
        QTest.keyClick(sacn_edit, Qt.Key.Key_Return)
        self.assertEqual(self.window.settings.dmx_in.sacn.universe, 9)

        # Switch back: Art-Net's universe should still be 7, untouched.
        QTest.mouseClick(tab.artnet_radio, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        self.assertEqual(self.window.settings.dmx_in.active, "artnet")
        self.assertEqual(self.window.settings.dmx_in.artnet.universe, 7)
        self.assertEqual(self.window.settings.dmx_in.sacn.universe, 9)


class OperatorTabStartStopTests(WindowTestCase):
    def test_start_click_runs_and_stop_click_stops(self):
        self.window.settings.dmx_out.active = "artnet"
        self.window.settings.dmx_out.artnet.target_ip = "127.0.0.1"

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.runner.running)
        self.assertFalse(self.window.tabs.isTabEnabled(1), "setup tabs should lock while running")

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.runner.running)
        self.assertTrue(self.window.tabs.isTabEnabled(1))

    def test_arm_checkbox_updates_runner(self):
        self.assertFalse(self.window.runner.armed)
        QTest.mouseClick(self.window.operator_tab.arm_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        self.assertTrue(self.window.runner.armed)


class LiveEndToEndUITests(WindowTestCase):
    def test_real_psn_updates_the_overview_table(self):
        settings = self.window.settings
        settings.psn_in.multicast = "236.10.10.13"
        settings.psn_in.port = 56577
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"
        settings.fixtures[0].marker_id = 1
        settings.fixtures[0].output_universe = 0

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.runner.running)
        QTest.mouseClick(self.window.operator_tab.arm_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        packet = build_psn_packet(1, 5.0, 0.0, 4.0)

        seen_live_row = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            sock.sendto(packet, (settings.psn_in.multicast, settings.psn_in.port))
            self.window._on_ui_tick()
            table = self.window.operator_tab.overview_table
            if table.rowCount() > 0:
                state_item = table.item(0, 5)
                if state_item and state_item.text() == "LIVE":
                    seen_live_row = True
                    break
            time.sleep(0.05)
        sock.close()

        self.assertTrue(seen_live_row, "overview table never showed a LIVE row for the tracked fixture")


if __name__ == "__main__":
    unittest.main()
