"""Small helpers for two-way binding a Qt widget directly to an attribute
on a settings dataclass instance. Values commit on blur/Enter (QLineEdit's
editingFinished) or immediately (checkboxes/combos), matching v1's
behaviour of committing text fields on focus-out rather than per-keystroke.
"""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit


def bind_float(line_edit: QLineEdit, obj, attr: str, lo=None, hi=None):
    line_edit.setText(f"{getattr(obj, attr):g}")

    def commit():
        try:
            value = float(line_edit.text())
        except ValueError:
            line_edit.setText(f"{getattr(obj, attr):g}")
            return
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        setattr(obj, attr, value)
        line_edit.setText(f"{value:g}")

    line_edit.editingFinished.connect(commit)
    return line_edit


def bind_int(line_edit: QLineEdit, obj, attr: str, lo=None, hi=None):
    line_edit.setText(str(getattr(obj, attr)))

    def commit():
        try:
            value = int(float(line_edit.text()))
        except ValueError:
            line_edit.setText(str(getattr(obj, attr)))
            return
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        setattr(obj, attr, value)
        line_edit.setText(str(value))

    line_edit.editingFinished.connect(commit)
    return line_edit


def bind_text(line_edit: QLineEdit, obj, attr: str):
    line_edit.setText(str(getattr(obj, attr)))

    def commit():
        setattr(obj, attr, line_edit.text())

    line_edit.editingFinished.connect(commit)
    return line_edit


def bind_checkbox(checkbox: QCheckBox, obj, attr: str):
    checkbox.setChecked(bool(getattr(obj, attr)))

    def commit(checked):
        setattr(obj, attr, bool(checked))

    checkbox.toggled.connect(commit)
    return checkbox


def bind_combo(combo: QComboBox, obj, attr: str, options):
    combo.addItems(options)
    current = str(getattr(obj, attr))
    if current in options:
        combo.setCurrentText(current)

    def commit(text):
        setattr(obj, attr, text)

    combo.currentTextChanged.connect(commit)
    return combo
