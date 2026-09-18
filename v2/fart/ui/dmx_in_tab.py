"""DMX In tab: exactly one of Art-Net/sACN is active at a time, but both
protocols' settings stay editable and persisted -- switching back and
forth never loses what was entered for the inactive one."""
from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QButtonGroup, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .binding import bind_int
from .dmx_channel_grid import DmxChannelGrid


class DMXInTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        dmx_in = main_window.settings.dmx_in

        layout = QVBoxLayout(self)

        protocol_box = QGroupBox("Active protocol")
        protocol_layout = QVBoxLayout(protocol_box)
        self.artnet_radio = QRadioButton("Art-Net")
        self.sacn_radio = QRadioButton("sACN")
        self.protocol_group = QButtonGroup(self)
        self.protocol_group.addButton(self.artnet_radio)
        self.protocol_group.addButton(self.sacn_radio)
        protocol_layout.addWidget(self.artnet_radio)
        protocol_layout.addWidget(self.sacn_radio)
        layout.addWidget(protocol_box)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        artnet_panel = QWidget()
        artnet_form = QFormLayout(artnet_panel)
        artnet_form.addRow("Universe", bind_int(QLineEdit(), dmx_in.artnet, "universe", lo=0, hi=32767))
        self.stack.addWidget(artnet_panel)

        sacn_panel = QWidget()
        sacn_form = QFormLayout(sacn_panel)
        sacn_form.addRow("Universe", bind_int(QLineEdit(), dmx_in.sacn, "universe", lo=1, hi=63999))
        self.stack.addWidget(sacn_panel)

        status_box = QGroupBox("Live input status")
        status_layout = QVBoxLayout(status_box)
        universe_row = QHBoxLayout()
        universe_row.addWidget(QLabel("Universe to monitor"))
        self.monitor_universe = QSpinBox()
        self.monitor_universe.setRange(0, 63999)
        self.monitor_universe.setValue(dmx_in.sacn.universe if dmx_in.active == "sacn" else dmx_in.artnet.universe)
        universe_row.addWidget(self.monitor_universe)
        universe_row.addStretch(1)
        status_layout.addLayout(universe_row)
        self.status_label = QLabel("No data received yet")
        status_layout.addWidget(self.status_label)
        self.channel_grid = DmxChannelGrid()
        status_layout.addWidget(self.channel_grid)
        layout.addWidget(status_box, 1)

        if dmx_in.active == "sacn":
            self.sacn_radio.setChecked(True)
            self.stack.setCurrentIndex(1)
        else:
            self.artnet_radio.setChecked(True)
            self.stack.setCurrentIndex(0)

        self.artnet_radio.toggled.connect(self._on_artnet_selected)

    def _on_artnet_selected(self, checked):
        if checked:
            self.main_window.settings.dmx_in.active = "artnet"
            self.stack.setCurrentIndex(0)
        else:
            self.main_window.settings.dmx_in.active = "sacn"
            self.stack.setCurrentIndex(1)

    def refresh(self):
        """Called from MainWindow's UI timer: shows whatever the active
        DMX-in plugin has actually received for the monitored universe,
        straight off the shared bus -- independent of any fixture, so it
        works as a plain protocol monitor even with no fixtures configured
        yet (the "like Artnetominator" ask)."""
        universe = self.monitor_universe.value()
        frames = self.main_window.runner.bus.snapshot()
        frame, ts = frames.get(universe, (None, 0.0))
        if frame is None:
            self.status_label.setText(f"No data received yet for universe {universe}")
        else:
            age = max(0.0, time.monotonic() - ts)
            self.status_label.setText(f"Universe {universe}: last packet {age:.1f}s ago")
        self.channel_grid.set_frame(frame)
