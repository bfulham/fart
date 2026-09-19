"""Browse and import a fixture type from GDTF Share (gdtf-share.com).

The real API requires a login for every call, including just listing the
catalog (verified live against the real service -- there is no anonymous
mode). So the closest this can get to "only log in when required" is:
a previously cached catalog listing is shown with no login at all, and a
real login prompt only appears the first time this machine ever browses
GDTF Share, or when the user explicitly clicks Refresh or Import and the
session has expired.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from ..gdtf import import_gdtf_channel_mapping
from ..gdtf_share import (
    GDTFShareClient, GDTFShareError, get_remembered_username, keychain_available,
    load_stored_password, remember_username, store_password,
)

CACHE_PATH = Path.home() / "FART2_gdtf_share_cache.json"


def _normalize_entry(raw):
    """Map one getList.php row (verified live against a real account) to
    the fields this dialog needs. Real shape: {"rid": int, "fixture": str,
    "manufacturer": str, "revision": str, "creator": str, "uploader":
    "Manuf."|"User", "rating": "N/A" or a numeric string, "modes":
    [{"name": str, "dmxfootprint": int}], ...}. getList.php itself returns
    {"result": true, "list": [...]}, already unwrapped by
    GDTFShareClient.get_list().
    """
    modes = []
    for m in raw.get("modes") or []:
        modes.append({"name": m.get("name") or "Mode", "footprint": m.get("dmxfootprint")})
    try:
        rating = float(raw.get("rating"))
    except (TypeError, ValueError):
        rating = 0.0
    return {
        "rid": raw.get("rid"),
        "manufacturer": raw.get("manufacturer") or "",
        "fixture": raw.get("fixture") or "",
        "revision": raw.get("revision") or "",
        "rating": rating,
        "verified": raw.get("uploader") == "Manuf.",
        "modes": modes,
    }


def _load_cache():
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return None


def _save_cache(catalog):
    try:
        CACHE_PATH.write_text(json.dumps(catalog))
    except Exception:
        pass


class GDTFShareLoginDialog(QDialog):
    def __init__(self, parent=None, username=None):
        super().__init__(parent)
        self.setWindowTitle("Log in to GDTF Share")
        self.username = None
        self.password = None

        layout = QVBoxLayout(self)
        note = ("A free GDTF Share account is required to browse fixtures. "
                "Register at gdtf-share.com if you don't have one yet.")
        if not keychain_available():
            note += "\n\nYour password can't be saved on this system, so you'll need to log in again next time."
        note_label = QLabel(note)
        note_label.setWordWrap(True)
        layout.addWidget(note_label)

        form = QFormLayout()
        self.username_edit = QLineEdit(username or "")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username", self.username_edit)
        form.addRow("Password", self.password_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            QMessageBox.warning(self, "FART", "Enter both a username and a password.")
            return
        self.username = username
        self.password = password
        self.accept()


class GDTFShareBrowseDialog(QDialog):
    def __init__(self, parent=None, client=None):
        super().__init__(parent)
        self.setWindowTitle("Browse GDTF Share")
        self.resize(520, 480)
        self.client = client or GDTFShareClient()
        self.catalog = []
        self.result_mapping = None
        self.result_mode_name = None
        self.result_label = None

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search manufacturer or fixture name")
        self.filter_edit.textChanged.connect(self._apply_filter)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._on_refresh)
        search_row.addWidget(self.filter_edit)
        search_row.addWidget(refresh_button)
        layout.addLayout(search_row)

        self.verified_only_checkbox = QCheckBox("Manufacturer-verified only")
        self.verified_only_checkbox.setChecked(True)
        self.verified_only_checkbox.stateChanged.connect(self._apply_filter)
        layout.addWidget(self.verified_only_checkbox)

        self.results_list = QListWidget()
        self.results_list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.results_list)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("DMX mode"))
        self.mode_combo = QComboBox()
        mode_row.addWidget(self.mode_combo, 1)
        layout.addLayout(mode_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = QPushButton("Import")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._on_import)
        buttons.addButton(self.import_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        cached = _load_cache()
        if cached:
            self.catalog = cached
            self._apply_filter()
        else:
            self._on_refresh()

    def _ensure_login(self):
        if self.client.logged_in:
            return True
        username = get_remembered_username()
        password = load_stored_password(username) if username else None
        if username and password:
            try:
                self.client.login(username, password)
                return True
            except GDTFShareError:
                pass
        dialog = GDTFShareLoginDialog(self, username=username)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            self.client.login(dialog.username, dialog.password)
        except GDTFShareError as exc:
            QMessageBox.critical(self, "FART", str(exc))
            return False
        remember_username(dialog.username)
        if not store_password(dialog.username, dialog.password):
            QMessageBox.information(
                self, "FART",
                "Your password could not be saved to this system's keychain, "
                "so you'll need to log in again next time.")
        return True

    def _on_refresh(self):
        if not self._ensure_login():
            return
        try:
            raw_catalog = self.client.get_list()
        except GDTFShareError as exc:
            QMessageBox.critical(self, "FART", str(exc))
            return
        self.catalog = [_normalize_entry(raw) for raw in raw_catalog]
        _save_cache(self.catalog)
        self._apply_filter()

    def _apply_filter(self):
        query = self.filter_edit.text().strip().lower()
        verified_only = self.verified_only_checkbox.isChecked()
        self.results_list.clear()
        for entry in self.catalog:
            if verified_only and not entry.get("verified"):
                continue
            haystack = f"{entry['manufacturer']} {entry['fixture']}".lower()
            if query and query not in haystack:
                continue
            label = f"{entry['manufacturer']} {entry['fixture']}"
            if entry.get("revision") and entry["revision"] != entry["fixture"]:
                label += f" ({entry['revision']})"
            if entry.get("verified"):
                label += " ✓"
            if entry.get("rating"):
                label += f" ★{entry['rating']:g}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results_list.addItem(item)

    def _on_selection_changed(self, row):
        self.mode_combo.clear()
        self.import_button.setEnabled(False)
        if not 0 <= row < self.results_list.count():
            return
        entry = self.results_list.item(row).data(Qt.ItemDataRole.UserRole)
        for mode in entry.get("modes") or []:
            footprint = mode.get("footprint")
            text = mode["name"] if footprint is None else f"{mode['name']} ({footprint}ch)"
            self.mode_combo.addItem(text, mode["name"])
        self.import_button.setEnabled(self.mode_combo.count() > 0)

    def _on_import(self):
        row = self.results_list.currentRow()
        if not 0 <= row < self.results_list.count():
            return
        entry = self.results_list.item(row).data(Qt.ItemDataRole.UserRole)
        mode_name = self.mode_combo.currentData()
        if not self._ensure_login():
            return
        self.status_label.setText("Downloading...")
        try:
            data = self.client.download_file(entry["rid"])
        except GDTFShareError as exc:
            self.status_label.setText("")
            QMessageBox.critical(self, "FART", str(exc))
            return
        fd, temp_path = tempfile.mkstemp(suffix=".gdtf")
        os.close(fd)
        try:
            with open(temp_path, "wb") as f:
                f.write(data)
            mapping, _modes, selected_mode = import_gdtf_channel_mapping(temp_path, 1, mode_name)
        except Exception as exc:
            self.status_label.setText("")
            QMessageBox.critical(self, "FART", f"Could not import that fixture: {exc}")
            return
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        self.result_mapping = mapping
        self.result_mode_name = selected_mode
        self.result_label = f"{entry['manufacturer']} {entry['fixture']}"
        self.accept()

