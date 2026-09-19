import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QDialog

from fart.gdtf_share import GDTFShareError
from fart.ui import gdtf_share_dialog
from fart.ui.gdtf_share_dialog import GDTFShareBrowseDialog, GDTFShareLoginDialog

_app = QApplication.instance() or QApplication(sys.argv)

GDTF_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<FixtureType>\n  <DMXModes>\n'
    '    <DMXMode Name="Standard 16ch">\n      <DMXChannels>\n'
    '        <DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer">'
    '<ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" DMXTo="255/1" />'
    '</LogicalChannel></DMXChannel>\n      </DMXChannels>\n    </DMXMode>\n'
    '  </DMXModes>\n</FixtureType>\n'
)


def make_gdtf_bytes():
    buf = tempfile.NamedTemporaryFile(suffix=".gdtf", delete=False)
    buf.close()
    with zipfile.ZipFile(buf.name, "w") as zf:
        zf.writestr("description.xml", GDTF_XML)
    data = Path(buf.name).read_bytes()
    os.unlink(buf.name)
    return data


# Shaped exactly like a real getList.php row (verified live against a real
# GDTF Share account): "dmxfootprint" (lowercase), "rating" as a numeric
# string or "N/A", "uploader" of "Manuf." or "User". This is what
# GDTFShareClient.get_list() returns (already unwrapped from the
# {"result": true, "list": [...]} envelope) -- the dialog normalizes it
# from there via _normalize_entry.
RAW_CATALOG = [
    {"rid": 1, "manufacturer": "Robe", "fixture": "MegaPointe", "revision": "1.2",
     "uploader": "Manuf.", "rating": "5.0", "modes": [{"name": "Standard 16ch", "dmxfootprint": 16}]},
    {"rid": 2, "manufacturer": "Martin", "fixture": "MAC Aura XB", "revision": "1.0",
     "uploader": "Manuf.", "rating": "4.0", "modes": [{"name": "Mode 1", "dmxfootprint": 20}]},
]

# What ends up in the on-disk cache and in dialog.catalog once _normalize_entry
# has run -- derived from RAW_CATALOG so the two can't drift apart.
NORMALIZED_CATALOG = [gdtf_share_dialog._normalize_entry(raw) for raw in RAW_CATALOG]


class FakeClient:
    def __init__(self, list_result=None, login_should_fail=False, download_bytes=None):
        self.logged_in = False
        self.username = None
        self.login_calls = []
        self.list_calls = 0
        self.download_calls = []
        self._list_result = list_result if list_result is not None else RAW_CATALOG
        self._login_should_fail = login_should_fail
        self._download_bytes = download_bytes if download_bytes is not None else make_gdtf_bytes()

    def login(self, username, password):
        self.login_calls.append((username, password))
        if self._login_should_fail:
            raise GDTFShareError("bad credentials")
        self.logged_in = True
        self.username = username

    def get_list(self):
        self.list_calls += 1
        return self._list_result

    def download_file(self, rid):
        self.download_calls.append(rid)
        return self._download_bytes


def _isolated_paths():
    tmp_dir = Path(tempfile.mkdtemp())
    return patch.multiple(
        gdtf_share_dialog,
        CACHE_PATH=tmp_dir / "cache.json",
    )


class FakeLoginDialog:
    """Stands in for GDTFShareLoginDialog so tests never open a real modal."""

    def __init__(self, parent=None, username=None):
        self.username = "alice"
        self.password = "secret"

    def exec(self):
        return QDialog.DialogCode.Accepted


class RejectingFakeLoginDialog(FakeLoginDialog):
    def exec(self):
        return QDialog.DialogCode.Rejected


class NormalizeEntryTests(unittest.TestCase):
    def test_real_field_names_are_mapped_correctly(self):
        raw = {"rid": 2294, "manufacturer": "ACME", "fixture": "ACME XP-260 BEAM", "revision": "rev1",
               "uploader": "Manuf.", "rating": "2.6",
               "modes": [{"name": "Mode 1 16 DMX", "dmxfootprint": 16}]}
        entry = gdtf_share_dialog._normalize_entry(raw)
        self.assertEqual(entry["rid"], 2294)
        self.assertEqual(entry["manufacturer"], "ACME")
        self.assertEqual(entry["fixture"], "ACME XP-260 BEAM")
        self.assertTrue(entry["verified"])
        self.assertEqual(entry["rating"], 2.6)
        self.assertEqual(entry["modes"], [{"name": "Mode 1 16 DMX", "footprint": 16}])

    def test_unrated_entry_and_user_upload_do_not_crash(self):
        raw = {"rid": 1, "manufacturer": "aa", "fixture": "1111111112", "revision": "x",
               "uploader": "User", "rating": "N/A", "modes": [{"name": "Standard mode", "dmxfootprint": 0}]}
        entry = gdtf_share_dialog._normalize_entry(raw)
        self.assertEqual(entry["rating"], 0.0)
        self.assertFalse(entry["verified"])


class GDTFShareBrowseDialogTests(unittest.TestCase):
    def test_verified_only_filter_hides_user_uploads_by_default(self):
        mixed = NORMALIZED_CATALOG + [gdtf_share_dialog._normalize_entry(
            {"rid": 9, "manufacturer": "Randomuser", "fixture": "Junk Fixture", "revision": "",
             "uploader": "User", "rating": "N/A", "modes": [{"name": "Mode", "dmxfootprint": 4}]})]
        with _isolated_paths():
            gdtf_share_dialog.CACHE_PATH.write_text(json.dumps(mixed))
            dialog = GDTFShareBrowseDialog(client=FakeClient())
            self.assertTrue(dialog.verified_only_checkbox.isChecked())
            self.assertEqual(dialog.results_list.count(), 2)
            dialog.verified_only_checkbox.setChecked(False)
            self.assertEqual(dialog.results_list.count(), 3)


    def test_no_cache_triggers_login_and_list_fetch(self):
        with _isolated_paths(), \
             patch("fart.ui.gdtf_share_dialog.get_remembered_username", return_value=None), \
             patch("fart.ui.gdtf_share_dialog.GDTFShareLoginDialog", FakeLoginDialog), \
             patch("fart.ui.gdtf_share_dialog.remember_username") as remember_username, \
             patch("fart.ui.gdtf_share_dialog.store_password", return_value=True) as store_password:
            client = FakeClient()
            dialog = GDTFShareBrowseDialog(client=client)
            self.assertTrue(client.logged_in)
            self.assertEqual(client.list_calls, 1)
            self.assertEqual(dialog.results_list.count(), 2)
            remember_username.assert_called_once_with("alice")
            store_password.assert_called_once_with("alice", "secret")

    def test_cached_catalog_shown_with_no_login_or_list_call(self):
        with _isolated_paths():
            gdtf_share_dialog.CACHE_PATH.write_text(json.dumps(NORMALIZED_CATALOG))
            client = FakeClient()
            dialog = GDTFShareBrowseDialog(client=client)
            self.assertFalse(client.logged_in)
            self.assertEqual(client.list_calls, 0)
            self.assertEqual(dialog.results_list.count(), 2)

    def test_filter_narrows_results_by_manufacturer_or_fixture(self):
        with _isolated_paths():
            gdtf_share_dialog.CACHE_PATH.write_text(json.dumps(NORMALIZED_CATALOG))
            dialog = GDTFShareBrowseDialog(client=FakeClient())
            dialog.filter_edit.setText("martin")
            self.assertEqual(dialog.results_list.count(), 1)
            self.assertIn("Martin", dialog.results_list.item(0).text())

    def test_selecting_a_result_populates_its_modes(self):
        with _isolated_paths():
            gdtf_share_dialog.CACHE_PATH.write_text(json.dumps(NORMALIZED_CATALOG))
            dialog = GDTFShareBrowseDialog(client=FakeClient())
            dialog.results_list.setCurrentRow(0)
            self.assertEqual(dialog.mode_combo.count(), 1)
            self.assertTrue(dialog.import_button.isEnabled())

    def test_import_downloads_and_applies_channel_mapping_then_accepts(self):
        with _isolated_paths():
            gdtf_share_dialog.CACHE_PATH.write_text(json.dumps(NORMALIZED_CATALOG))
            client = FakeClient()
            client.logged_in = True
            dialog = GDTFShareBrowseDialog(client=client)
            dialog.results_list.setCurrentRow(0)
            with patch.object(dialog, "accept") as accept:
                dialog._on_import()
            accept.assert_called_once()
            self.assertEqual(client.download_calls, [1])
            self.assertIn("dimmer", dialog.result_mapping)
            self.assertEqual(dialog.result_mode_name, "Standard 16ch")
            self.assertEqual(dialog.result_label, "Robe MegaPointe")

    def test_cancelling_login_leaves_list_empty_and_does_not_raise(self):
        with _isolated_paths(), \
             patch("fart.ui.gdtf_share_dialog.get_remembered_username", return_value=None), \
             patch("fart.ui.gdtf_share_dialog.GDTFShareLoginDialog", RejectingFakeLoginDialog):
            client = FakeClient()
            dialog = GDTFShareBrowseDialog(client=client)
            self.assertFalse(client.logged_in)
            self.assertEqual(dialog.results_list.count(), 0)


class GDTFShareLoginDialogTests(unittest.TestCase):
    def test_submitting_with_a_blank_password_shows_a_warning_and_does_not_accept(self):
        with patch("fart.ui.gdtf_share_dialog.keychain_available", return_value=True):
            dialog = GDTFShareLoginDialog(username="alice")
        dialog.password_edit.setText("")
        with patch("fart.ui.gdtf_share_dialog.QMessageBox.warning") as warning:
            dialog._on_accept()
        warning.assert_called_once()
        self.assertIsNone(dialog.username)
        self.assertIsNone(dialog.password)

    def test_valid_credentials_are_captured_and_dialog_accepts(self):
        with patch("fart.ui.gdtf_share_dialog.keychain_available", return_value=True):
            dialog = GDTFShareLoginDialog(username="alice")
        dialog.password_edit.setText("hunter2")
        with patch.object(dialog, "accept") as accept:
            dialog._on_accept()
        accept.assert_called_once()
        self.assertEqual(dialog.username, "alice")
        self.assertEqual(dialog.password, "hunter2")


if __name__ == "__main__":
    unittest.main()
