"""DMX In tab: exactly one of Art-Net/sACN is active at a time, but both
protocols' settings stay editable and persisted -- switching back and
forth never loses what was entered for the inactive one."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup, QFormLayout, QGroupBox, QLineEdit, QRadioButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .binding import bind_int


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

        layout.addStretch(1)

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
