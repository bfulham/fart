"""Alternate aiming controls for the calibration wizard: a top-down pan
dial and a side-view tilt arc, each dragged directly (the needle *is* the
control, not a separate slider) -- an easier way to see which way a
fixture is actually pointing than reading raw slider percentages.

The tilt arc's range always matches the specific fixture's own
tilt_min/tilt_max, linearly mapped onto a fixed 180-degree visual sweep
(so an asymmetric range like -45..135 still lands its midpoint at the
bottom) -- real fixtures vary widely in tilt range, but the dial shape
stays a clean, consistent semicircle regardless. Pan stays a plain
-180..180 dial rather than trying to match a fixture's real (often wider
than 360-degree) pan range, which a single-turn dial can't represent
without ambiguity.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class PanDial(QWidget):
    """Top-down view: 0 at top (facing upstage), positive angles clockwise
    (house right), matching the app's own bearing convention."""

    valueChanged = Signal(float)

    def __init__(self, lo=-180.0, hi=180.0):
        super().__init__()
        self.lo, self.hi = lo, hi
        self._value = 0.0
        self._dragging = False
        self.setMinimumSize(150, 150)

    def value(self):
        return self._value

    def setValue(self, value, _emit=True):
        value = max(self.lo, min(self.hi, value))
        if value == self._value:
            return
        self._value = value
        self.update()
        if _emit:
            self.valueChanged.emit(value)

    def _geometry(self):
        side = min(self.width(), self.height())
        cx, cy = self.width() / 2, self.height() / 2
        return cx, cy, max(10.0, side / 2 - 18)

    def _angle_from_pos(self, pos):
        cx, cy, _r = self._geometry()
        dx, dy = pos.x() - cx, pos.y() - cy
        return math.degrees(math.atan2(dx, -dy))

    def mousePressEvent(self, event):
        self._dragging = True
        self.setValue(self._angle_from_pos(event.position()))

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.setValue(self._angle_from_pos(event.position()))

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def paintEvent(self, event):
        cx, cy, r = self._geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.palette().mid().color()
        text_color = self.palette().text().color()
        accent = self.palette().highlight().color()

        painter.setPen(QPen(mid, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r, r)

        painter.setPen(text_color)
        painter.drawText(QPointF(cx - 5, cy - r - 4), "0")
        painter.drawText(QPointF(cx + r + 4, cy + 4), "90")
        painter.drawText(QPointF(cx - 10, cy + r + 14), "180")
        painter.drawText(QPointF(cx - r - 22, cy + 4), "-90")

        rad = math.radians(self._value)
        nx, ny = cx + r * 0.82 * math.sin(rad), cy - r * 0.82 * math.cos(rad)
        painter.setPen(QPen(accent, 3))
        painter.drawLine(QPointF(cx, cy), QPointF(nx, ny))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(nx, ny), 7, 7)

        painter.setPen(QPen(mid, 1))
        painter.setBrush(self.palette().button())
        painter.drawRect(int(cx - 9), int(cy - 9), 18, 18)


class TiltArc(QWidget):
    """Side view: a fixture hanging from a truss, needle sweeping through
    an arc below it. lo/hi is that specific fixture's own tilt_min/
    tilt_max, drawn at its true angular width -- tilt 0 always points
    straight down, and everything else is drawn at its real angle from
    there, so a wide range (e.g. -135..135, a 270-degree total swing)
    visibly swings up past horizontal on both sides rather than being
    squeezed into a generic semicircle."""

    valueChanged = Signal(float)

    def __init__(self, lo=-135.0, hi=135.0):
        super().__init__()
        self.lo, self.hi = lo, hi
        self._value = 0.0
        self._dragging = False
        self.setMinimumSize(190, 190)

    def value(self):
        return self._value

    def set_range(self, lo, hi):
        self.lo, self.hi = lo, hi
        self.setValue(self._value, _emit=False)
        self.update()

    def setValue(self, value, _emit=True):
        lo, hi = min(self.lo, self.hi), max(self.lo, self.hi)
        value = max(lo, min(hi, value))
        if value == self._value:
            return
        self._value = value
        self.update()
        if _emit:
            self.valueChanged.emit(value)

    def _geometry(self):
        w, h = self.width(), self.height()
        pivot_y = min(h * 0.35, 70.0)
        r = min(w / 2 - 20, pivot_y - 20, h - pivot_y - 30)
        return w / 2, pivot_y, max(10.0, r)

    def _display_angle(self, value):
        # Direct 1:1 mapping: 0 is always straight down (90 in the
        # atan2-style convention used here, measuring from the positive
        # x-axis through the bottom), independent of lo/hi -- so the
        # visual angle always matches the real one.
        return 90.0 - value

    def _value_from_display_angle(self, angle):
        return 90.0 - angle

    def _arc_display_range(self):
        # hi maps to the smaller display angle (further right/up), lo to
        # the larger one (further left/up) -- see _display_angle.
        return self._display_angle(self.hi), self._display_angle(self.lo)

    def _angle_from_pos(self, pos):
        cx, cy, _r = self._geometry()
        dx, dy = pos.x() - cx, pos.y() - cy
        raw = math.degrees(math.atan2(dy, dx))
        start, end = self._arc_display_range()

        def distance_outside(angle):
            if angle < start:
                return start - angle
            if angle > end:
                return angle - end
            return 0.0

        best = min((raw, raw + 360.0, raw - 360.0), key=distance_outside)
        angle = max(start, min(end, best))
        return self._value_from_display_angle(angle)

    def mousePressEvent(self, event):
        self._dragging = True
        self.setValue(self._angle_from_pos(event.position()))

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.setValue(self._angle_from_pos(event.position()))

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def paintEvent(self, event):
        cx, cy, r = self._geometry()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.palette().mid().color()
        text_color = self.palette().text().color()
        accent = self.palette().highlight().color()

        start, end = self._arc_display_range()
        # Arc is drawn as an explicit polyline (not QPainter.drawArc) so it
        # uses exactly the same angle math as the needle -- no risk of the
        # visible arc and the draggable range disagreeing.
        steps = max(8, int((end - start) / 4))
        points = []
        for i in range(steps + 1):
            angle = math.radians(start + (end - start) * i / steps)
            points.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
        painter.setPen(QPen(mid, 1.5))
        painter.drawPolyline(QPolygonF(points))
        painter.setPen(QPen(mid, 1))
        painter.drawLine(QPointF(cx - r - 10, cy), QPointF(cx + r + 10, cy))

        painter.setPen(text_color)

        def label_at(display_angle, text, above):
            rad = math.radians(display_angle)
            lx = cx + (r + 12) * math.cos(rad) - 4 * len(text)
            ly = cy + (r + 12) * math.sin(rad) + (-4 if above else 12)
            painter.drawText(QPointF(lx, ly), text)

        label_at(start, f"{self.hi:g}", self.hi > 0)
        label_at(end, f"{self.lo:g}", self.lo > 0)
        if min(self.lo, self.hi) <= 0 <= max(self.lo, self.hi):
            painter.drawText(QPointF(cx - 4, cy + r + 16), "0")

        rad = math.radians(self._display_angle(self._value))
        nx, ny = cx + r * 0.88 * math.cos(rad), cy + r * 0.88 * math.sin(rad)
        painter.setPen(QPen(accent, 3))
        painter.drawLine(QPointF(cx, cy), QPointF(nx, ny))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(nx, ny), 7, 7)

        painter.setPen(QPen(mid, 1))
        painter.setBrush(self.palette().button())
        painter.drawRect(int(cx - 9), int(cy - 9), 18, 18)
