"""Real UI tests: QTest synthesizes actual clicks/typing into actual Qt
widgets, entirely in-process (QT_QPA_PLATFORM=offscreen) -- unlike v1's
Tkinter UI, which needed OS-level Accessibility permission to automate and
so was never actually click-tested this session.
"""
import errno
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


def multicast_sendto(sock, data, addr):
    """Some sandboxed CI network namespaces (seen on GitHub's hosted macOS
    runner) have no route to any multicast destination at all -- not even
    via loopback -- and every sendto() there fails immediately with
    ENETUNREACH/EHOSTUNREACH, unlike a real machine, where it's delivered
    over loopback normally. Skip rather than fail when that's the case."""
    try:
        sock.sendto(data, addr)
    except OSError as exc:
        if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            raise unittest.SkipTest(f"real multicast send unavailable in this environment: {exc}") from exc
        raise


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

    def test_reclicking_the_only_fixture_switches_editor_back_from_type_view(self):
        """Real bug: with only one fixture, clicking it after the editor had
        switched to showing a fixture type doesn't change the Fixtures
        list's current row (it was already row 0), so Qt's
        currentRowChanged signal never fires and the editor stays stuck on
        the type view. itemClicked (fires on every click, not just row
        changes) is what actually fixes this."""
        tab = self.window.fixtures_tab
        while len(self.window.settings.fixtures) > 1:
            tab._on_remove()
        self.assertEqual(tab.list_widget.count(), 1)

        tab._on_duplicate_type()
        self.assertEqual(tab.editor_mode, "type")

        rect = tab.list_widget.visualItemRect(tab.list_widget.item(0))
        QTest.mouseClick(tab.list_widget.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual(tab.editor_mode, "fixture",
                          "clicking the only (already-current) fixture must still switch the editor back")


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
        # Setup tabs stay usable while running (no lockout) -- users found
        # the previous lock annoying, e.g. wanting to check fixture
        # settings without stopping output first.
        self.assertTrue(self.window.tabs.isTabEnabled(1))

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
            multicast_sendto(sock, packet, (settings.psn_in.multicast, settings.psn_in.port))
            self.window._on_ui_tick()
            table = self.window.operator_tab.overview_table
            if table.rowCount() > 0:
                state_item = table.item(0, 7)  # column order: Light, Marker, Marker XYZ, Pan/Tilt, Distance, DMX In Mode, DMX In Marker, State
                if state_item and state_item.text() == "LIVE":
                    seen_live_row = True
                    break
            time.sleep(0.05)
        sock.close()

        self.assertTrue(seen_live_row, "overview table never showed a LIVE row for the tracked fixture")


class DMXMonitorTests(WindowTestCase):
    """Real-socket proof for the "like Artnetominator" live channel grids
    added to the DMX In and DMX Out tabs: they must show actual bytes that
    were actually received/sent, not just repaint on a timer."""

    def test_dmx_in_tab_channel_grid_shows_real_received_data(self):
        settings = self.window.settings
        # Forces the control-input plugin to actually start -- otherwise
        # nothing is listening and the grid would stay all-zero regardless
        # of whether the widget itself works.
        settings.fader.source = "dmx_in"
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.runner.running)

        from fart.plugins._artnet import ARTNET_PORT, build_artnet_dmx

        # ArtNetInPlugin binds all interfaces, not just loopback (correct
        # for real use -- it needs to receive from the LAN). On a network
        # with real Art-Net gear, universe 0 can carry genuine unrelated
        # traffic; a distinctive high universe number avoids that, and
        # matching both bytes exactly (not just "any nonzero") confirms
        # this is actually our own test packet.
        monitor_universe = 500
        self.window.dmx_in_tab.monitor_universe.setValue(monitor_universe)
        console_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        frame = bytearray(512)
        frame[0] = 111
        frame[1] = 222
        packet = build_artnet_dmx(monitor_universe, frame)

        seen = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            console_sock.sendto(packet, ("127.0.0.1", ARTNET_PORT))
            self.window.dmx_in_tab.refresh()
            values = self.window.dmx_in_tab.channel_grid._last_values
            if values[0] == 111 and values[1] == 222:
                seen = True
                break
            time.sleep(0.05)
        console_sock.close()

        self.assertTrue(seen, "DMX In tab's channel grid never showed real received Art-Net data")
        self.assertEqual(self.window.dmx_in_tab.channel_grid._last_values[1], 222)

    def test_dmx_out_tab_channel_grid_shows_real_sent_data(self):
        settings = self.window.settings
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.30", 56650
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"
        settings.fixtures[0].marker_id = 1
        settings.fixtures[0].output_universe = 0

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.operator_tab.arm_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

        psn_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        psn_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        packet = build_psn_packet(1, 5.0, 0.0, 4.0)

        seen = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            multicast_sendto(psn_sock, packet, (settings.psn_in.multicast, settings.psn_in.port))
            self.window.dmx_out_tab.refresh()
            # Pan/tilt (channels 1-4) should become nonzero once the runner
            # actually aims this fixture at the tracked marker.
            if any(self.window.dmx_out_tab.channel_grid._last_values[:4]):
                seen = True
                break
            time.sleep(0.05)
        psn_sock.close()

        self.assertTrue(seen, "DMX Out tab's channel grid never showed real sent data")


class OperatorOverviewDMXInColumnsTests(WindowTestCase):
    """Real end-to-end proof for the Operator tab's "DMX In Mode"/"DMX In
    Marker" columns: a fixture with console relay configured should show
    the console's actual live mode/marker selection, and a plain fixture
    with no relay configured should show "--" for both, not a misleading
    guess."""

    def test_console_relay_fixture_shows_live_mode_and_marker(self):
        settings = self.window.settings
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.40", 56660
        settings.dmx_in.active = "artnet"
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"
        settings.fixtures[0].marker_id = 1
        settings.fixtures[0].output_universe = 0
        settings.fixtures[0].console_mode_channel = 10
        settings.fixtures[0].console_marker_channel = 11

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.operator_tab.arm_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

        from fart.plugins._artnet import ARTNET_PORT, build_artnet_dmx

        psn_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        psn_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        psn_packet = build_psn_packet(1, 5.0, 0.0, 4.0)
        psn_packet_marker3 = build_psn_packet(3, -2.0, 1.0, 3.0)

        console_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        console_frame = bytearray(512)
        console_frame[9] = 255   # mode channel (10): >=128 => auto
        console_frame[10] = 3    # marker channel (11): select marker 3
        console_packet = build_artnet_dmx(0, console_frame)

        seen = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            multicast_sendto(psn_sock, psn_packet, (settings.psn_in.multicast, settings.psn_in.port))
            multicast_sendto(psn_sock, psn_packet_marker3, (settings.psn_in.multicast, settings.psn_in.port))
            console_sock.sendto(console_packet, ("127.0.0.1", ARTNET_PORT))
            self.window._on_ui_tick()
            table = self.window.operator_tab.overview_table
            if table.rowCount() > 0:
                mode_item = table.item(0, 5)
                marker_item = table.item(0, 6)
                if mode_item and marker_item and mode_item.text() == "Auto" and marker_item.text() == "3":
                    seen = True
                    break
            time.sleep(0.05)
        psn_sock.close()
        console_sock.close()

        self.assertTrue(seen, "overview table never showed the console's live DMX In mode/marker for the relayed fixture")

    def test_fixture_without_console_relay_shows_dashes(self):
        settings = self.window.settings
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.41", 56661
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"
        settings.fixtures[0].marker_id = 1
        settings.fixtures[0].output_universe = 0
        settings.fixtures[0].console_mode_channel = 0
        settings.fixtures[0].console_marker_channel = 0

        QTest.mouseClick(self.window.operator_tab.start_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(self.window.operator_tab.arm_checkbox, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        packet = build_psn_packet(1, 5.0, 0.0, 4.0)

        seen = False
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            multicast_sendto(sock, packet, (settings.psn_in.multicast, settings.psn_in.port))
            self.window._on_ui_tick()
            table = self.window.operator_tab.overview_table
            if table.rowCount() > 0:
                mode_item = table.item(0, 5)
                marker_item = table.item(0, 6)
                if mode_item and marker_item and mode_item.text() == "—" and marker_item.text() == "—":
                    seen = True
                    break
            time.sleep(0.05)
        sock.close()

        self.assertTrue(seen, "overview table should show '—' for DMX In Mode/Marker when no console relay is configured")


if __name__ == "__main__":
    unittest.main()
