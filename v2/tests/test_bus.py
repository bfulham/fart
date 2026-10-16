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


class ExternalInputBusRateTests(unittest.TestCase):
    def test_rate_and_age_for_a_slow_feed(self):
        bus = ExternalInputBus()
        now = __import__("time").monotonic()
        bus.arrivals[3] = __import__("collections").deque([now - 6.0, now - 4.0, now - 2.0])
        bus.frames[3] = (bytes(512), now - 2.0)
        rate, age = bus.rates()[3]
        self.assertAlmostEqual(rate, 0.5, delta=0.01)  # one packet every 2 s
        self.assertAlmostEqual(age, 2.0, delta=0.1)

    def test_single_packet_has_zero_rate(self):
        bus = ExternalInputBus()
        bus.update(1, bytes(512))
        rate, age = bus.rates()[1]
        self.assertEqual(rate, 0.0)
        self.assertLess(age, 1.0)

    def test_unseen_universe_is_absent(self):
        self.assertNotIn(7, ExternalInputBus().rates())


class TrackerBankDetailTests(unittest.TestCase):
    def test_detail_combines_position_derived_speed_extras_name_and_age(self):
        bank = TrackerBank()
        bank.update(4, 1.0, 2.0, 3.0)
        bank.update_extras(4, speed=(1.0, 0.0, 0.0), status=1.0)
        bank.set_name(4, "Lead")
        detail = bank.snapshot_detail()[4]
        self.assertEqual(detail["position"], (1.0, 2.0, 3.0))
        self.assertEqual(detail["speed"], (1.0, 0.0, 0.0))
        self.assertEqual(detail["status"], 1.0)
        self.assertEqual(detail["name"], "Lead")
        self.assertLess(detail["age"], 1.0)

    def test_derived_velocity_comes_from_consecutive_positions(self):
        bank = TrackerBank()
        bank.xyz[1] = (0.0, 0.0, 0.0, 100.0)
        bank.update(1, 1.0, 0.0, 0.0)
        self.assertIsNotNone(bank.snapshot_detail()[1]["derived_velocity"])

    def test_rate_over_a_window(self):
        import collections
        import time
        bank = TrackerBank()
        now = time.monotonic()
        bank.arrivals[2] = collections.deque([now - 4.0, now - 3.0, now - 2.0, now - 1.0, now])
        self.assertAlmostEqual(bank.snapshot_detail()[2]["rate"], 1.0, delta=0.01)

    def test_clear_forgets_everything(self):
        bank = TrackerBank()
        bank.update(1, 0, 0, 0)
        bank.update_extras(1, speed=(0, 0, 0))
        bank.set_name(1, "x")
        bank.clear()
        self.assertEqual(bank.snapshot_detail(), {})
        self.assertEqual(bank.get(1), (0.0, 0.0, 0.0, 0.0))

    def test_existing_snapshot_is_unchanged(self):
        bank = TrackerBank()
        bank.update(1, 1.0, 2.0, 3.0)
        bank.update_extras(1, speed=(1, 1, 1))
        self.assertEqual(list(bank.snapshot()), [1])
        self.assertEqual(len(bank.snapshot()[1]), 4)

