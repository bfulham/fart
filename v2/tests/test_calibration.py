import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.calibration import solve_fixture_calibration
from fart.config import FixtureConfig, FixtureType
from fart.engine import calculate_aim, resolve_fixture

PAN_MIN, PAN_MAX = -270.0, 270.0
TILT_MIN, TILT_MAX = -135.0, 135.0
FIXTURE_TYPE = FixtureType(pan_min=PAN_MIN, pan_max=PAN_MAX, tilt_min=TILT_MIN, tilt_max=TILT_MAX)


class CalibrationSolverTests(unittest.TestCase):
    def test_recovers_fixture_position_and_zero_angles(self):
        true_fixture = FixtureConfig(x=0.0, y=-8.0, z=5.0, pan_zero_bearing=5.0, tilt_zero_elevation=-2.0)
        resolved_true = resolve_fixture(true_fixture, FIXTURE_TYPE)
        targets = [(0, 0, 0), (5, 0, 0), (-5, 0, 0), (0, 5, 0), (0, -5, 0), (0, 0, 1.7)]
        samples = []
        for target in targets:
            _bearing, _elevation, pan, tilt, _distance = calculate_aim(resolved_true, *target)
            samples.append((*target, pan, tilt))

        start_fixture = FixtureConfig(x=0.0, y=-8.0, z=5.0, pan_zero_bearing=0.0, tilt_zero_elevation=0.0)
        solved, rms = solve_fixture_calibration(start_fixture, PAN_MIN, PAN_MAX, samples)
        self.assertLess(rms, 0.1)
        self.assertAlmostEqual(solved.x, true_fixture.x, delta=0.05)
        self.assertAlmostEqual(solved.y, true_fixture.y, delta=0.05)
        self.assertAlmostEqual(solved.z, true_fixture.z, delta=0.1)
        self.assertAlmostEqual(solved.pan_zero_bearing, true_fixture.pan_zero_bearing, delta=0.1)
        self.assertAlmostEqual(solved.tilt_zero_elevation, true_fixture.tilt_zero_elevation, delta=0.3)

    def test_too_few_samples_raises(self):
        fixture = FixtureConfig()
        with self.assertRaises(ValueError):
            solve_fixture_calibration(fixture, PAN_MIN, PAN_MAX, [(0, 0, 0, 0, 0)] * 3)


if __name__ == "__main__":
    unittest.main()
