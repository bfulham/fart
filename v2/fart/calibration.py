"""Fixture position/angle calibration solver. Ported from fart.py v1
(solve_fixture_calibration and its helpers) with behaviour unchanged.

Given several (known XYZ point, captured pan, captured tilt) samples for a
fixture, estimates that fixture's real optical-centre position and its
pan-zero-bearing/tilt-zero-elevation, trying pan/tilt direction
combinations and rejecting solutions that don't fit well enough to apply
safely.
"""
from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace

from .config import FixtureConfig
from .engine import calculate_aim, wrap180


def _solve_3x3(matrix, vector):
    """Small dependency-free 3x3 linear solver."""
    a = [list(map(float, row)) + [float(vector[i])] for i, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-9:
            raise ValueError("Calibration geometry is degenerate; use wider-spaced target points")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        for j in range(col, 4):
            a[col][j] /= div
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            for j in range(col, 4):
                a[row][j] -= factor * a[col][j]
    return [a[i][3] for i in range(3)]


def _direction_from_bearing_elevation(bearing, elevation):
    b = math.radians(bearing)
    e = math.radians(elevation)
    ce = math.cos(e)
    return (math.sin(b) * ce, math.cos(b) * ce, math.sin(e))


def _closest_point_to_lines(line_points, line_dirs):
    """Return the point closest to all 3D lines using normal equations."""
    m = [[0.0, 0.0, 0.0] for _ in range(3)]
    v = [0.0, 0.0, 0.0]
    for point, direction in zip(line_points, line_dirs):
        dx, dy, dz = direction
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-9:
            continue
        dx, dy, dz = dx / length, dy / length, dz / length
        proj = [
            [1.0 - dx * dx, -dx * dy, -dx * dz],
            [-dy * dx, 1.0 - dy * dy, -dy * dz],
            [-dz * dx, -dz * dy, 1.0 - dz * dz],
        ]
        px, py, pz = point
        for r in range(3):
            for c in range(3):
                m[r][c] += proj[r][c]
            v[r] += proj[r][0] * px + proj[r][1] * py + proj[r][2] * pz
    return _solve_3x3(m, v)


def solve_fixture_calibration(base_fixture: FixtureConfig, pan_min: float, pan_max: float, samples):
    """Estimate fixture position and physical pan/tilt mapping from aimed samples.

    Converts each captured pan/tilt value into a 3D ray aimed at a known
    point, then triangulates the fixture position from the reverse rays.
    Also tries pan/tilt direction combinations, rejects high-error
    solutions, and returns the direction combination that best matches
    the data. Returns (solved: FixtureConfig, ray_rms: float).

    pan_min/pan_max now live on the fixture's FixtureType (not the instance
    itself), but calculate_aim needs them to pick the best-fit wrapped pan
    candidate -- passed in explicitly rather than requiring a full resolved
    fixture, since nothing else about the type matters for this solve.
    """
    if len(samples) < 4:
        raise ValueError("At least four calibration points are required")
    points = [(float(x), float(y), float(z), float(pan), float(tilt)) for x, y, z, pan, tilt in samples]
    target_points = [(p[0], p[1], p[2]) for p in points]
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    max_z = max(p[2] for p in points)

    xy_margin = 60.0
    if base_fixture.z > max_z + 1.0 and base_fixture.z < 60.0:
        z_low = max(-2.0, min(max_z + 0.2, base_fixture.z - 20.0))
        z_high = max(max_z + 35.0, base_fixture.z + 20.0)
    else:
        z_low = -2.0
        z_high = max(max_z + 35.0, 30.0)

    def make_fixture(position, pan_zero, tilt_zero, pan_dir, tilt_dir):
        # A throwaway object with just what calculate_aim needs (pan_min/
        # pan_max now live on the fixture's FixtureType, not the instance,
        # so they're passed in explicitly rather than pulled off base_fixture).
        x, y, z = position
        return SimpleNamespace(
            x=x, y=y, z=z,
            pan_zero_bearing=wrap180(pan_zero), tilt_zero_elevation=tilt_zero,
            pan_direction=1 if pan_dir >= 0 else -1, tilt_direction=1 if tilt_dir >= 0 else -1,
            pan_offset=0.0, tilt_offset=0.0, pan_min=pan_min, pan_max=pan_max,
        )

    def candidate_from_zeros(pan_zero, tilt_zero, pan_dir, tilt_dir):
        dirs = []
        for _tx, _ty, _tz, observed_pan, observed_tilt in points:
            bearing = pan_zero + (observed_pan - base_fixture.pan_offset) / (pan_dir or 1)
            elevation = tilt_zero + (observed_tilt - base_fixture.tilt_offset) / (tilt_dir or 1)
            dirs.append(_direction_from_bearing_elevation(bearing, elevation))
        position = _closest_point_to_lines(target_points, dirs)
        return position, dirs

    def score_candidate(position, dirs, pan_zero, tilt_zero, pan_dir, tilt_dir):
        x, y, z = position
        ray_total = 0.0
        total = 0.0
        for (tx, ty, tz, observed_pan, observed_tilt), direction in zip(points, dirs):
            vx, vy, vz = tx - x, ty - y, tz - z
            dx, dy, dz = direction
            along = vx * dx + vy * dy + vz * dz
            closest = (x + dx * along, y + dy * along, z + dz * along)
            err = math.dist((tx, ty, tz), closest)
            ray_total += err * err
            total += err * err
            if along <= 0:
                total += 5000.0 + abs(along) * 100.0
            f = make_fixture(position, pan_zero, tilt_zero, pan_dir, tilt_dir)
            try:
                _b, _e, pan, tilt, _d = calculate_aim(f, tx, ty, tz, observed_pan)
                total += (wrap180(pan - observed_pan) * 0.03) ** 2 + ((tilt - observed_tilt) * 0.03) ** 2
            except Exception:
                total += 1e6
        if not (min_x - xy_margin <= x <= max_x + xy_margin):
            total += (min(abs(x - (min_x - xy_margin)), abs(x - (max_x + xy_margin))) * 50.0) ** 2
        if not (min_y - xy_margin <= y <= max_y + xy_margin):
            total += (min(abs(y - (min_y - xy_margin)), abs(y - (max_y + xy_margin))) * 50.0) ** 2
        if z < z_low:
            total += ((z_low - z) * 80.0) ** 2
        if z > z_high:
            total += ((z - z_high) * 80.0) ** 2
        if -2.0 <= base_fixture.z <= 60.0 and base_fixture.z > max_z + 0.5:
            total += ((z - base_fixture.z) / 25.0) ** 2
        ray_rms = math.sqrt(ray_total / max(1, len(points)))
        return total / len(points), ray_rms

    def loss_for_zeros(pan_zero, tilt_zero, pan_dir, tilt_dir):
        try:
            position, dirs = candidate_from_zeros(pan_zero, tilt_zero, pan_dir, tilt_dir)
            loss, ray_rms = score_candidate(position, dirs, pan_zero, tilt_zero, pan_dir, tilt_dir)
            return loss, ray_rms, position
        except Exception:
            return 1e12, float("inf"), None

    base_pan_dir = 1 if base_fixture.pan_direction >= 0 else -1
    base_tilt_dir = 1 if base_fixture.tilt_direction >= 0 else -1
    direction_candidates = []
    for combo in ((base_pan_dir, base_tilt_dir), (-base_pan_dir, base_tilt_dir),
                  (base_pan_dir, -base_tilt_dir), (-base_pan_dir, -base_tilt_dir)):
        if combo not in direction_candidates:
            direction_candidates.append(combo)

    starts = []
    for p0 in (base_fixture.pan_zero_bearing, 0.0, 90.0, -90.0, 180.0, -180.0, 30.0, -30.0):
        for t0 in (base_fixture.tilt_zero_elevation, -90.0, -45.0, 0.0, 45.0, 90.0):
            starts.append((wrap180(p0), t0))

    best = None
    best_loss = float("inf")
    for pan_dir, tilt_dir in direction_candidates:
        for start_pan, start_tilt in starts:
            params = [float(start_pan), float(start_tilt)]
            steps = [45.0, 30.0]
            current, ray_rms, pos = loss_for_zeros(params[0], params[1], pan_dir, tilt_dir)
            for _ in range(160):
                improved = False
                for i in range(2):
                    for sign in (1.0, -1.0):
                        trial = params[:]
                        trial[i] += steps[i] * sign
                        if i == 0:
                            trial[i] = wrap180(trial[i])
                        value, trial_rms, trial_pos = loss_for_zeros(trial[0], trial[1], pan_dir, tilt_dir)
                        if value < current:
                            params, current, ray_rms, pos, improved = trial, value, trial_rms, trial_pos, True
                if not improved:
                    steps = [step * 0.55 for step in steps]
                    if max(steps) < 0.001:
                        break
            if current < best_loss and pos is not None:
                best = (params[:], pos, pan_dir, tilt_dir, ray_rms)
                best_loss = current

    if best is None:
        raise ValueError("Calibration failed; check captured points and pan/tilt directions")
    (pan_zero, tilt_zero), position, pan_dir, tilt_dir, ray_rms = best
    x, y, z = position
    if z > z_high + 0.5 or z < z_low - 0.5:
        raise ValueError(
            f"Calibration result was physically implausible: Z={z:.2f} m. "
            "Check tilt direction and capture points, or seed the fixture with an approximate height first."
        )
    if ray_rms > 1.0:
        raise ValueError(
            f"Calibration did not fit well enough to apply safely: fit error {ray_rms:.2f} m. "
            "Recapture wider-spaced points, check the selected fixture, and confirm pan/tilt channels are not swapped."
        )
    result = make_fixture(position, pan_zero, tilt_zero, pan_dir, tilt_dir)
    solved = replace(
        base_fixture,
        x=round(result.x, 4), y=round(result.y, 4), z=round(result.z, 4),
        pan_zero_bearing=round(wrap180(result.pan_zero_bearing), 4),
        tilt_zero_elevation=round(result.tilt_zero_elevation, 4),
        pan_direction=result.pan_direction, tilt_direction=result.tilt_direction,
        pan_offset=0.0, tilt_offset=0.0,
    )
    return solved, ray_rms
