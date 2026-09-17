# Fixture calibration

Calibrate each fixture independently, with its lamp disabled or shutter closed.

## Before calibration

The calibration wizard's own faders directly drive the selected fixture, so FART needs its DMX channels before it will show them.

1. Patch the exact fixture model and mode in the console or visualiser.
2. Enter the absolute DMX slots for pan coarse/fine, tilt coarse/fine, dimmer, and optional shutter.
3. Enter the fixture personality's real pan and tilt ranges. A nominal 540° pan personality normally maps to `-270` through `+270`.
4. Set the reverse pan/tilt direction roughly correctly if known — the solver can flip it during solving, but a reasonable starting guess helps it converge faster.
5. Stop the main FART output before starting calibration output; the two cannot run at the same time.

## Calibration wizard workflow

1. On the Setup: Lights tab, select one or more fixtures and click **Calibrate selected light**, or use **Open fixture calibration wizard** on the Setup: Calibration tab.
2. If a selected fixture's required DMX channels are incomplete, FART opens **DMX setup before calibration** first. Fill in pan coarse, tilt coarse, dimmer, and any fine/shutter channels, then continue.
3. Click **Start calibration output**, keeping the dimmer fader low.
4. Select a known point from the list (or add a custom XYZ point), move each selected fixture's pan/tilt faders — plus the fine nudge buttons — until its beam hits that point, then click **Capture point for all fixtures**.
5. Repeat for at least four points; five or six is recommended. Spread them across house left/right, upstage/downstage, centre, and at least one raised point.
6. Click **Solve and apply all**. With multiple fixtures selected, each is solved independently against the same shared point list; the solve runs in the background, so the wizard (including **Stop output / blackout**) stays responsive while it works.
7. Check the reported fit error and the 3D preview, then save settings.

The solver estimates each fixture's optical-centre XYZ, pan-zero bearing, and tilt-zero elevation, and can also flip pan/tilt direction if the initial guess was wrong. Pan and tilt trim offsets are reset to zero when a solution is applied. If the solver reports the fit didn't converge well enough, or the estimated height looks physically implausible, recapture with wider-spaced points rather than trusting a poor fit.

FART calculates the exact line from each fixture's optical centre to the marker; correct pointing still depends on accurate channel maps, a correct pan/tilt direction, accurate personality angle ranges, and known, accurately measured calibration target positions.
