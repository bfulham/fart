import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from fart.ui.pan_tilt_dial import PanDial, TiltArc

_app = QApplication.instance() or QApplication(sys.argv)


class PanDialTests(unittest.TestCase):
    def test_straight_up_is_zero_degrees(self):
        dial = PanDial()
        dial.resize(200, 200)
        cx, cy, r = dial._geometry()
        self.assertAlmostEqual(dial._angle_from_pos(QPointF(cx, cy - r)), 0.0, places=3)

    def test_house_right_is_90_degrees(self):
        dial = PanDial()
        dial.resize(200, 200)
        cx, cy, r = dial._geometry()
        self.assertAlmostEqual(dial._angle_from_pos(QPointF(cx + r, cy)), 90.0, places=3)

    def test_set_value_clamps_to_range_and_emits(self):
        dial = PanDial(-180.0, 180.0)
        seen = []
        dial.valueChanged.connect(seen.append)
        dial.setValue(500.0)
        self.assertEqual(dial.value(), 180.0)
        self.assertEqual(seen, [180.0])

    def test_set_value_with_emit_false_does_not_emit(self):
        dial = PanDial()
        seen = []
        dial.valueChanged.connect(seen.append)
        dial.setValue(45.0, _emit=False)
        self.assertEqual(dial.value(), 45.0)
        self.assertEqual(seen, [])


class TiltArcTests(unittest.TestCase):
    def test_midpoint_of_range_points_straight_down(self):
        arc = TiltArc(-45.0, 135.0)
        self.assertAlmostEqual(arc._display_angle(45.0), 90.0, places=3)

    def test_lo_is_left_hi_is_right(self):
        arc = TiltArc(-45.0, 135.0)
        self.assertAlmostEqual(arc._display_angle(-45.0), 180.0, places=3)
        self.assertAlmostEqual(arc._display_angle(135.0), 0.0, places=3)

    def test_value_and_display_angle_round_trip_for_an_asymmetric_range(self):
        arc = TiltArc(-135.0, 135.0)
        for v in (-135.0, -60.0, 0.0, 72.5, 135.0):
            angle = arc._display_angle(v)
            self.assertAlmostEqual(arc._value_from_display_angle(angle), v, places=3)

    def test_set_range_updates_bounds_and_reclamps_current_value(self):
        arc = TiltArc(-135.0, 135.0)
        arc.setValue(130.0)
        arc.set_range(-45.0, 100.0)
        self.assertEqual((arc.lo, arc.hi), (-45.0, 100.0))
        self.assertEqual(arc.value(), 100.0, "the old value (130) is now out of range and must reclamp to the new max")


if __name__ == "__main__":
    unittest.main()
