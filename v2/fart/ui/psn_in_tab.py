"""PSN In tab: OpenFollow PSN receive settings, plus a live table of every
tracker being received -- whether or not any fixture uses it -- including
fields FART itself never reads (speed, orientation, acceleration, target,
status), so a misbehaving marker can be diagnosed from here."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .binding import bind_float, bind_int, bind_text

COLUMNS = ["ID", "Name", "Position (m)", "Speed (sent)", "Speed (derived)", "Orientation",
           "Acceleration", "Target", "Status", "Rate (Hz)", "Age (s)", "Used by"]
LOST_COLOR = QColor(220, 60, 60)


def _vec(value, fmt="{:.2f}"):
    if value is None:
        return "—"
    return ", ".join(fmt.format(v) for v in value)


def _magnitude(value):
    if value is None:
        return "—"
    return f"{(value[0] ** 2 + value[1] ** 2 + value[2] ** 2) ** 0.5:.2f} m/s"


def tracker_row(marker_id, info, used_by):
    """The table cells for one tracker, as strings. 'Speed (sent)' is what
    the sender included itself; 'Speed (derived)' is computed from
    consecutive positions, so it still shows for senders that omit speed."""
    sent = info.get("speed")
    derived = info.get("derived_velocity")
    status = info.get("status")
    age = info.get("age")
    return [
        str(marker_id),
        info.get("name") or "—",
        _vec(info.get("position")),
        f"{_magnitude(sent)} ({_vec(sent)})" if sent is not None else "—",
        _magnitude(derived),
        _vec(info.get("orientation")),
        _vec(info.get("accel")),
        _vec(info.get("target")),
        "—" if status is None else f"{status:.2f}",
        f"{info.get('rate', 0.0):.1f}",
        "—" if age is None else f"{age:.1f}",
        ", ".join(used_by) if used_by else "—",
    ]


class PSNInTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        psn = main_window.settings.psn_in

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        form.addRow("PSN multicast group", bind_text(QLineEdit(), psn, "multicast"))
        form.addRow("PSN UDP port", bind_int(QLineEdit(), psn, "port", lo=1, hi=65535))
        form.addRow("PSN interface IP", bind_text(QLineEdit(), psn, "interface"))
        form.addRow("Default PSN marker", bind_int(QLineEdit(), psn, "default_marker_id", lo=0))
        form.addRow("Tracking timeout (s)", bind_float(QLineEdit(), psn, "timeout_s", lo=0.01))
        form.addRow("Smoothing (0-0.95)", bind_float(QLineEdit(), psn, "smoothing", lo=0.0, hi=0.95))
        form.addRow("Lead/lag (ms)", bind_float(QLineEdit(), psn, "lead_lag_ms", lo=-5000, hi=5000))

        header = QHBoxLayout()
        self.summary_label = QLabel("Start FART to receive PSN")
        header.addWidget(self.summary_label, stretch=1)
        clear_button = QPushButton("Clear table")
        clear_button.setToolTip("Forget every tracker seen so far. Live ones reappear on their next packet.")
        clear_button.clicked.connect(self._on_clear_clicked)
        header.addWidget(clear_button)
        layout.addLayout(header)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

    def _on_clear_clicked(self):
        self.main_window.runner.trackers.clear()
        self.refresh()

    def _fixtures_using(self, marker_id):
        return [f.name for f in self.main_window.settings.fixtures
                if f.enabled and int(f.marker_id) == marker_id]

    def refresh(self):
        """Called from MainWindow's UI timer."""
        runner = self.main_window.runner
        plugin = runner.position_plugin
        if plugin is not None:
            stats = plugin.stats()
            age = "never" if stats["last_packet_age"] is None else f"{stats['last_packet_age']:.1f}s ago"
            system = f" from '{runner.trackers.system_name}'" if runner.trackers.system_name else ""
            self.summary_label.setText(
                f"Receiving{system}: {stats['packets']} packets, {stats['positions']} positions (last packet {age})")
        else:
            self.summary_label.setText("Start FART to receive PSN")

        timeout = self.main_window.settings.psn_in.timeout_s
        detail = runner.trackers.snapshot_detail()
        ids = sorted(detail)
        self.table.setRowCount(len(ids))
        for row, marker_id in enumerate(ids):
            info = detail[marker_id]
            cells = tracker_row(marker_id, info, self._fixtures_using(marker_id))
            lost = info.get("age") is None or info["age"] > timeout
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if lost and COLUMNS[col] == "Age (s)":
                    item.setForeground(LOST_COLOR)
                self.table.setItem(row, col, item)
