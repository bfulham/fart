"""DMX Out tab: exactly one of Art-Net/sACN/Open DMX is active at a time,
same persist-everything pattern as DMX In."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup, QFormLayout, QGroupBox, QLineEdit, QRadioButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .binding import bind_int, bind_text


class DMXOutTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        dmx_out = main_window.settings.dmx_out

        layout = QVBoxLayout(self)

        protocol_box = QGroupBox("Active protocol")
        protocol_layout = QVBoxLayout(protocol_box)
        self.artnet_radio = QRadioButton("Art-Net")
        self.sacn_radio = QRadioButton("sACN")
        self.open_dmx_radio = QRadioButton("Open DMX (ENTTEC USB)")
        self.protocol_group = QButtonGroup(self)
        for button in (self.artnet_radio, self.sacn_radio, self.open_dmx_radio):
            self.protocol_group.addButton(button)
            protocol_layout.addWidget(button)
        layout.addWidget(protocol_box)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        artnet_panel = QWidget()
        artnet_form = QFormLayout(artnet_panel)
        artnet_form.addRow("Target IP", bind_text(QLineEdit(), dmx_out.artnet, "target_ip"))
        self.stack.addWidget(artnet_panel)

        sacn_panel = QWidget()
        sacn_form = QFormLayout(sacn_panel)
        self.sacn_universes_edit = QLineEdit(", ".join(str(u) for u in dmx_out.sacn.universes))
        self.sacn_universes_edit.editingFinished.connect(self._on_sacn_universes_changed)
        sacn_form.addRow("Universes (comma-separated)", self.sacn_universes_edit)
        self.stack.addWidget(sacn_panel)

        open_dmx_panel = QWidget()
        open_dmx_form = QFormLayout(open_dmx_panel)
        open_dmx_form.addRow("Adapters (e.g. COM3=0, COM4=1)", bind_text(QLineEdit(), dmx_out.open_dmx, "adapters"))
        open_dmx_form.addRow("Fallback port", bind_text(QLineEdit(), dmx_out.open_dmx, "fallback_port"))
        self.stack.addWidget(open_dmx_panel)

        layout.addStretch(1)

        index_for_active = {"artnet": 0, "sacn": 1, "open_dmx": 2}
        index = index_for_active.get(dmx_out.active, 0)
        [self.artnet_radio, self.sacn_radio, self.open_dmx_radio][index].setChecked(True)
        self.stack.setCurrentIndex(index)

        self.artnet_radio.toggled.connect(lambda checked: checked and self._select("artnet", 0))
        self.sacn_radio.toggled.connect(lambda checked: checked and self._select("sacn", 1))
        self.open_dmx_radio.toggled.connect(lambda checked: checked and self._select("open_dmx", 2))

    def _select(self, active, index):
        self.main_window.settings.dmx_out.active = active
        self.stack.setCurrentIndex(index)

    def _on_sacn_universes_changed(self):
        text = self.sacn_universes_edit.text()
        try:
            universes = sorted({int(part.strip()) for part in text.split(",") if part.strip()})
        except ValueError:
            universes = self.main_window.settings.dmx_out.sacn.universes
        if not universes:
            universes = self.main_window.settings.dmx_out.sacn.universes
        self.main_window.settings.dmx_out.sacn.universes = universes
        self.sacn_universes_edit.setText(", ".join(str(u) for u in universes))
