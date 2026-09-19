"""Real UI tests: QTest synthesizes actual clicks/typing into actual Qt
widgets, entirely in-process (QT_QPA_PLATFORM=offscreen) -- unlike v1's
Tkinter UI, which needed OS-level Accessibility permission to automate and
so was never actually click-tested this session.
"""
import errno
import json
import os
import socket
import struct
import sys
import tempfile
import time
import unittest
import unittest.mock
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fart.ui.calibration_wizard import DEFAULT_TARGETS, CalibrationWizard
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
        self.config_path = Path(self._tmp.name) / "FART2.fart"
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


class MainWindowConfigPathTests(unittest.TestCase):
    """MainWindow's legacy-file bootstrap never touches the real home
    directory in these tests -- DEFAULT_CONFIG_FILE/LEGACY_CONFIG_FILE are
    patched to a temp dir for every case."""

    def _make_window(self, default_path, legacy_path):
        with unittest.mock.patch("fart.ui.main_window.DEFAULT_CONFIG_FILE", default_path), \
             unittest.mock.patch("fart.ui.main_window.LEGACY_CONFIG_FILE", legacy_path):
            window = MainWindow(config_path=None)
        window.ui_timer.stop()
        # deleteLater(), not close(): close() runs closeEvent -> a real
        # save -- pointless here (these tests only cover the bootstrap
        # load path) and unsafe once the caller's tmp dir is gone.
        self.addCleanup(window.deleteLater)
        return window

    def test_fresh_install_uses_the_new_default_path_with_no_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            default_path = Path(tmp) / "FART2.fart"
            legacy_path = Path(tmp) / "FART2.json"
            window = self._make_window(default_path, legacy_path)
            self.assertEqual(window.config_path, default_path)
            self.assertEqual(window.settings.fixtures[0].name, "Light 1")

    def test_legacy_json_is_loaded_but_future_saves_go_to_the_new_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            default_path = Path(tmp) / "FART2.fart"
            legacy_path = Path(tmp) / "FART2.json"
            legacy_path.write_text(json.dumps({"output": "sACN", "fixtures": [{"name": "Legacy Light"}]}))
            window = self._make_window(default_path, legacy_path)
            self.assertEqual(window.config_path, default_path,
                              "future saves must target the new .fart path, not the legacy one")
            self.assertEqual(window.settings.fixtures[0].name, "Legacy Light")
            self.assertFalse(default_path.exists(), "loading must not itself create the new file")

    def test_existing_default_path_takes_priority_over_legacy(self):
        from fart.config import Settings, save_settings
        with tempfile.TemporaryDirectory() as tmp:
            default_path = Path(tmp) / "FART2.fart"
            legacy_path = Path(tmp) / "FART2.json"
            legacy_path.write_text(json.dumps({"fixtures": [{"name": "Legacy Light"}]}))
            current = Settings()
            current.fixtures[0].name = "Current Light"
            save_settings(default_path, current)
            window = self._make_window(default_path, legacy_path)
            self.assertEqual(window.settings.fixtures[0].name, "Current Light")


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

    def test_browse_gdtf_share_creates_a_new_type_rather_than_editing_the_selected_one(self):
        # The real GDTFShareBrowseDialog talks to the network and the OS
        # keychain -- stand in for it here so this test (like every other
        # UI test) never does either.
        tab = self.window.fixtures_tab
        existing_type = self.window.settings.fixture_types[tab.selected_type_index]
        existing_snapshot = asdict(existing_type)
        before_count = len(self.window.settings.fixture_types)

        class _Code:
            Accepted = 1

        class FakeDialog:
            DialogCode = _Code
            result_mapping = {"dimmer": 3, "pan_coarse": 1, "footprint": 16}
            result_mode_name = "Standard 16ch"
            result_label = "Robe MegaPointe"

            def __init__(self, parent=None):
                pass

            def exec(self):
                return 1

        with unittest.mock.patch("fart.ui.fixtures_tab.GDTFShareBrowseDialog", FakeDialog), \
             unittest.mock.patch("fart.ui.fixtures_tab.QMessageBox.information"):
            tab._on_browse_gdtf_share_clicked()

        self.assertEqual(asdict(existing_type), existing_snapshot,
                          "the previously-selected type must be untouched")
        self.assertEqual(len(self.window.settings.fixture_types), before_count + 1)
        new_type = self.window.settings.fixture_types[-1]
        self.assertEqual(new_type.name, "Robe MegaPointe")
        self.assertEqual(new_type.dimmer, 3)
        self.assertEqual(new_type.pan_coarse, 1)
        self.assertEqual(new_type.footprint, 16)
        self.assertEqual(tab.selected_type_index, len(self.window.settings.fixture_types) - 1)

    def test_gdtf_buttons_live_under_the_type_list_not_the_type_editor(self):
        from PySide6.QtWidgets import QPushButton
        tab = self.window.fixtures_tab
        tab._load_type(0)
        editor_buttons = [b.text() for b in tab.editor_container.findChildren(QPushButton)]
        self.assertNotIn("Import from GDTF…", editor_buttons)
        self.assertNotIn("Browse GDTF Share…", editor_buttons)
        top_level_buttons = [b.text() for b in tab.findChildren(QPushButton)
                              if b not in tab.editor_container.findChildren(QPushButton)]
        self.assertIn("Import from GDTF…", top_level_buttons)
        self.assertIn("Browse GDTF Share…", top_level_buttons)

    def test_import_from_gdtf_file_creates_a_new_type_rather_than_editing_the_selected_one(self):
        import tempfile
        import zipfile

        gdtf_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<FixtureType>\n  <DMXModes>\n'
            '    <DMXMode Name="Basic">\n      <DMXChannels>\n'
            '        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer">'
            '<ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" DMXTo="255/1" />'
            '</LogicalChannel></DMXChannel>\n      </DMXChannels>\n    </DMXMode>\n'
            '  </DMXModes>\n</FixtureType>\n'
        )
        tmp = tempfile.NamedTemporaryFile(suffix=".gdtf", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w") as zf:
            zf.writestr("description.xml", gdtf_xml)

        tab = self.window.fixtures_tab
        existing_type = self.window.settings.fixture_types[tab.selected_type_index]
        existing_snapshot = asdict(existing_type)
        before_count = len(self.window.settings.fixture_types)

        with unittest.mock.patch("fart.ui.fixtures_tab.QFileDialog.getOpenFileName", return_value=(tmp.name, "")), \
             unittest.mock.patch("fart.ui.fixtures_tab.QMessageBox.information"):
            tab._on_import_gdtf_clicked()

        self.assertEqual(asdict(existing_type), existing_snapshot,
                          "the previously-selected type must be untouched")
        self.assertEqual(len(self.window.settings.fixture_types), before_count + 1)
        new_type = self.window.settings.fixture_types[-1]
        self.assertEqual(new_type.name, Path(tmp.name).stem)
        self.assertEqual(new_type.dimmer, 1)
        os.unlink(tmp.name)


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


class CalibrationTabTests(WindowTestCase):
    def test_refresh_list_preserves_multi_selection(self):
        """Real user report: selecting multiple fixtures and opening the
        calibration wizard only showed one. _on_open_wizard() calls
        _refresh_list() right before reading the selection (to pick up any
        fixtures added/renamed elsewhere), and _refresh_list() used to
        unconditionally reset the selection to row 0 -- collapsing any
        multi-fixture selection every single time the wizard was opened."""
        tab = self.window.calibration_tab
        while len(self.window.settings.fixtures) < 3:
            self.window.fixtures_tab._on_add()
        tab._refresh_list()
        self.assertEqual(tab.list_widget.count(), 3)

        tab.list_widget.item(0).setSelected(True)
        tab.list_widget.item(2).setSelected(True)
        self.assertEqual({i.row() for i in tab.list_widget.selectedIndexes()}, {0, 2})

        # This is exactly what _on_open_wizard does before reading the
        # selection.
        tab._refresh_list()
        self.assertEqual({i.row() for i in tab.list_widget.selectedIndexes()}, {0, 2},
                          "refreshing the list must not collapse a multi-fixture selection")


class CalibrationWizardTests(WindowTestCase):
    """The guided flow: setup (default/custom points) -> step through every
    (point, fixture) pair, lighting only the current fixture -> done page.
    Verified with a real Art-Net output socket, not just internal state,
    since the whole point of the guided flow is that only one fixture is
    actually lit on the wire at any given moment.
    """

    def _two_fixture_settings(self):
        settings = self.window.settings
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"
        while len(settings.fixtures) < 2:
            self.window.fixtures_tab._on_add()
        settings.fixtures[0].output_universe = 0
        settings.fixtures[0].output_start_address = 1
        settings.fixtures[1].output_universe = 0
        settings.fixtures[1].output_start_address = 11  # no channel overlap with fixture 0
        return settings

    def test_guided_flow_lights_only_the_current_fixture_and_captures_all_pairs(self):
        from fart.plugins._artnet import ARTNET_PORT, parse_artnet_dmx

        settings = self._two_fixture_settings()
        wizard = CalibrationWizard(self.window, [0, 1])
        sniffer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sniffer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sniffer.bind(("", ARTNET_PORT))
        sniffer.settimeout(0.1)

        def latest_frame_for_universe(universe, timeout=3.0):
            # recvfrom() blocks the whole thread, so the wizard's own
            # output QTimer (which is what actually sends anything) never
            # gets a chance to fire unless the Qt event loop runs in
            # between attempts -- qWait() pumps it.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                QTest.qWait(15)
                try:
                    data, _addr = sniffer.recvfrom(2048)
                except socket.timeout:
                    continue
                parsed = parse_artnet_dmx(data)
                if parsed and parsed[0] == universe:
                    return parsed[1]
            return None

        try:
            self.assertTrue(wizard.default_radio.isChecked())
            wizard._on_start_guided()
            self.assertIs(wizard.stack.currentWidget(), wizard.guided_page)
            self.assertTrue(wizard.output_running)
            self.assertEqual(wizard.fixture_pos, 0)
            self.assertEqual(wizard.point_index, 0)

            frame = latest_frame_for_universe(0)
            self.assertIsNotNone(frame, "never received a decoded Art-Net frame for universe 0")
            self.assertGreater(frame[4], 0, "fixture 0 (the active one) should be lit")
            self.assertEqual(frame[14], 0, "fixture 1 (not yet its turn) must stay blacked out")

            wizard._on_guided_next()
            self.assertEqual(wizard.fixture_pos, 1)
            self.assertEqual(len(wizard.samples[0]), 1)

            frame = latest_frame_for_universe(0)
            self.assertIsNotNone(frame)
            self.assertEqual(frame[4], 0, "fixture 0 must black out once it's no longer the active fixture")
            self.assertGreater(frame[14], 0, "fixture 1 should now be the lit one")

            # Undo that step and confirm it doesn't leave a duplicate sample.
            wizard._on_guided_back()
            self.assertEqual(wizard.fixture_pos, 0)
            self.assertEqual(len(wizard.samples[0]), 0, "going back must undo the capture for that step")

            # Walk through every remaining (point, fixture) pair.
            total_steps = len(DEFAULT_TARGETS) * 2
            for _ in range(total_steps):
                wizard._on_guided_next()

            self.assertIs(wizard.stack.currentWidget(), wizard.done_page)
            self.assertFalse(wizard.output_running, "output should stop once every pair is captured")
            self.assertEqual(len(wizard.samples[0]), len(DEFAULT_TARGETS))
            self.assertEqual(len(wizard.samples[1]), len(DEFAULT_TARGETS))
        finally:
            sniffer.close()
            wizard._stop_output_internal()
            wizard.output_timer.stop()
            wizard.deleteLater()
            QTest.qWait(20)

    def test_custom_points_are_used_instead_of_defaults(self):
        wizard = CalibrationWizard(self.window, [0])
        # Needs real geometry for QTest.mouseClick's hit-testing to land at
        # all -- without show(), a freshly constructed dialog's widgets can
        # have degenerate 0x0 rects.
        wizard.show()
        QTest.qWait(50)
        try:
            # A QRadioButton's real clickable hit-region is only its
            # natural content size (indicator + label), even when a layout
            # stretches its allocated rect wider -- clicking the default
            # centre lands in dead space and silently misses.
            QTest.mouseClick(wizard.custom_radio, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
            self.assertTrue(wizard.custom_points_box.isEnabled())

            wizard.setup_x_edit.setText("1.5")
            wizard.setup_y_edit.setText("-2.5")
            wizard.setup_z_edit.setText("3.0")
            wizard._on_add_setup_point()
            self.assertEqual(wizard.custom_point_list.count(), 1)
            self.assertEqual(wizard._custom_points, [("Point 1", 1.5, -2.5, 3.0)])

            # A bad entry must not silently add a broken point. Patched
            # since QMessageBox.critical() is a real modal call that would
            # otherwise block this test forever waiting for a click nothing
            # in a headless test can provide.
            with unittest.mock.patch("fart.ui.calibration_wizard.QMessageBox.critical") as mock_critical:
                wizard.setup_x_edit.setText("not a number")
                wizard._on_add_setup_point()
            mock_critical.assert_called_once()
            self.assertEqual(wizard.custom_point_list.count(), 1)

            self.window.settings.dmx_out.active = "artnet"
            self.window.settings.dmx_out.artnet.target_ip = "127.0.0.1"
            wizard._on_start_guided()
            self.assertEqual(wizard.points, [("Point 1", 1.5, -2.5, 3.0)])
        finally:
            wizard._stop_output_internal()
            wizard.output_timer.stop()
            wizard.deleteLater()
            QTest.qWait(20)

    def test_cancel_returns_to_setup_and_stops_output(self):
        self.window.settings.dmx_out.active = "artnet"
        self.window.settings.dmx_out.artnet.target_ip = "127.0.0.1"
        wizard = CalibrationWizard(self.window, [0])
        try:
            wizard._on_start_guided()
            self.assertTrue(wizard.output_running)
            wizard._on_cancel_guided()
            self.assertIs(wizard.stack.currentWidget(), wizard.setup_page)
            self.assertFalse(wizard.output_running)
        finally:
            wizard._stop_output_internal()
            wizard.output_timer.stop()
            wizard.deleteLater()
            QTest.qWait(20)


class PanTiltDialTests(WindowTestCase):
    """The "Aim (dial)" tab added to each fixture row: drag the needle
    directly instead of reading a linear slider. The old sliders stay the
    source of truth pan()/tilt() read from, with the dial just another
    control kept in sync in both directions."""

    def test_dial_and_slider_stay_in_sync_both_directions(self):
        wizard = CalibrationWizard(self.window, [0])
        wizard.show()
        QTest.qWait(50)
        try:
            row = wizard.rows[0]
            cx, cy, r = row.pan_dial._geometry()
            # A real press-drag-release from straight up (0) to house
            # right (90), simulating an actual mouse drag on the needle --
            # not just calling setValue() directly.
            QTest.mousePress(row.pan_dial, Qt.MouseButton.LeftButton, pos=QPoint(int(cx), int(cy - r)))
            QTest.mouseMove(row.pan_dial, pos=QPoint(int(cx + r), int(cy)))
            QTest.mouseRelease(row.pan_dial, Qt.MouseButton.LeftButton, pos=QPoint(int(cx + r), int(cy)))
            self.assertAlmostEqual(row.pan_dial.value(), 90.0, delta=1.0)
            self.assertAlmostEqual(row.pan_slider.value() / 100.0, row.pan_dial.value(), delta=0.1,
                                    msg="dragging the dial must update the paired slider")

            # And the reverse direction: moving the old slider updates the
            # dial too, so whichever tab you look at is always current.
            row.pan_slider.setValue(-9000)
            self.assertAlmostEqual(row.pan_dial.value(), -90.0, delta=0.1,
                                    msg="moving the slider must update the dial")
        finally:
            wizard._stop_output_internal()
            wizard.output_timer.stop()
            wizard.deleteLater()
            QTest.qWait(20)

    def test_tilt_arc_range_matches_this_fixtures_own_tilt_range(self):
        fixture_type = self.window.settings.fixture_types[0]
        fixture_type.tilt_min = -45.0
        fixture_type.tilt_max = 100.0
        wizard = CalibrationWizard(self.window, [0])
        try:
            row = wizard.rows[0]
            self.assertEqual((row.tilt_dial.lo, row.tilt_dial.hi), (-45.0, 100.0),
                              "the tilt dial must reflect this fixture's real tilt range, not a generic default")
        finally:
            wizard._stop_output_internal()
            wizard.output_timer.stop()
            wizard.deleteLater()
            QTest.qWait(20)


if __name__ == "__main__":
    unittest.main()
