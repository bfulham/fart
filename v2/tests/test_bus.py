import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.bus import ExternalInputBus, FaderState, TrackerBank


class TrackerBankTests(unittest.TestCase):
    def test_update_and_get(self):
        bank = TrackerBank()
        bank.update(1, 1.0, 2.0, 3.0)
        x, y, z, t = bank.get(1)
        self.assertEqual((x, y, z), (1.0, 2.0, 3.0))
        self.assertGreater(t, 0.0)

    def test_get_unknown_marker_defaults_to_origin_with_zero_timestamp(self):
        bank = TrackerBank()
        self.assertEqual(bank.get(99), (0.0, 0.0, 0.0, 0.0))

    def test_predict_extrapolates_from_velocity(self):
        bank = TrackerBank()
        bank.xyz[1] = (0.0, 0.0, 0.0, 100.0)
        bank.vel[1] = (2.0, 0.0, 0.0)
        x, y, z, t = bank.predict(1, 0.5)
        self.assertAlmostEqual(x, 1.0)
        self.assertEqual(t, 100.0)


class FaderStateTests(unittest.TestCase):
    def test_clamped_to_0_1(self):
        fader = FaderState()
        fader.update(1.5)
        self.assertEqual(fader.get()[0], 1.0)
        fader.update(-1.0)
        self.assertEqual(fader.get()[0], 0.0)


class ExternalInputBusTests(unittest.TestCase):
    def test_update_pads_to_512_and_get_channel_reads_back(self):
        bus = ExternalInputBus()
        bus.update(3, bytes([10, 20, 30]))
        frame, ts = bus.get(3)
        self.assertEqual(len(frame), 512)
        self.assertEqual(frame[:3], bytes([10, 20, 30]))
        self.assertGreater(ts, 0.0)
        value, age = bus.get_channel(3, 2)
        self.assertEqual(value, 20)
        self.assertIsNotNone(age)

    def test_unknown_universe_returns_none(self):
        bus = ExternalInputBus()
        self.assertEqual(bus.get(5), (None, 0.0))
        self.assertEqual(bus.get_channel(5, 1), (None, None))

    def test_get_channel_out_of_range_returns_none(self):
        bus = ExternalInputBus()
        bus.update(1, bytes([1, 2, 3]))
        self.assertEqual(bus.get_channel(1, 0), (None, None))
        self.assertEqual(bus.get_channel(1, 513), (None, None))


if __name__ == "__main__":
    unittest.main()
