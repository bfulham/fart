# Fixture calibration

Calibrate each fixture with its lamp disabled or shutter closed.

## Before calibration

The wizard drives the real fixture directly, so FART needs to know the fixture's DMX channels and ranges first.

1. Patch the exact fixture model and mode in the console or visualiser.
2. On the **Fixtures** tab, make sure the fixture's **type** has the right channel offsets for pan coarse/fine, tilt coarse/fine and dimmer (and a shutter, if it has one), and the fixture itself has the right **DMX Out patch** (output universe and start address). A GDTF-backed type fills these in for you; pick the DMX mode the fixture is actually running.
3. Enter the fixture personality's real pan and tilt ranges on the type. A nominal 540° pan personality normally maps to `-270` through `+270`.
4. Set the pan/tilt direction roughly correctly if you know it. The solver can flip a wrong direction, but a reasonable starting guess helps it converge.
5. **Stop the main FART output first.** The wizard has its own output, and the two cannot run at the same time.

## Wizard workflow

1. On the **Calibration** tab, select one or more fixtures (Ctrl/Shift-click for several) and click **Open fixture calibration wizard**.
2. Choose which points to calibrate against: the **default points** (six standard positions: centre, house right, house left, upstage and downstage on the floor, and centre at head height), or **custom points** you enter as X/Y/Z in the [coordinate convention](../README.md#coordinate-convention). Click **Start calibration**.
3. The wizard steps through every (point, fixture) pair, one at a time, cycling through all fixtures before moving on to the next point. **Only the fixture whose turn it is is lit** (at whatever its faders say); every other fixture stays blacked out, even if it shares a universe with the active one.
4. Aim the active fixture at the point shown. There are two ways to aim:
   - **Sliders** tab: pan, tilt, dimmer, zoom and iris sliders.
   - **Aim (dial)** tab: drag the needle on the top-down pan dial or the side-view tilt arc. The tilt arc always matches this fixture's own tilt range. Hold **Shift** while dragging for fine control (a tenth of the mouse's speed, without the needle jumping).
5. Fine-tune with the **Fine adjust** panel below either tab: exact numeric pan and tilt entry, and **−/+** buttons that move by the selected step (1°, 0.1° or 0.01°). The arrow keys do the same (Left/Right for pan, Up/Down for tilt), with **Shift** for a tenth of the step and **Ctrl** for ten times it. The arrow keys are left alone while you are typing in a box or choosing from a list.
6. Click **Capture & Next →**. **← Back** undoes the last capture; **Cancel / blackout** stops the output and returns to the setup page.
7. When every point has been captured for every fixture, click **Solve and apply all**. Each fixture is solved independently against the same shared point list.
8. Check the reported fit error, then run FART and check the aim. Settings are saved when you close the app.

At least four points are required; five or six is better. Spread them across house left/right, upstage/downstage, centre, and include at least one raised point.

The solver estimates each fixture's optical-centre XYZ, pan-zero bearing and tilt-zero elevation, and can flip the pan/tilt direction if the starting guess was wrong. Pan and tilt trim offsets are reset to zero when a solution is applied. If the solver reports that the fit didn't converge, or the estimated height looks physically implausible, recapture with wider-spaced points rather than trusting a poor fit.

FART calculates the exact line from each fixture's optical centre to the marker; correct pointing still depends on accurate channel maps, a correct pan/tilt direction, accurate personality angle ranges, and known, accurately measured calibration target positions.
