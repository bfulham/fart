# Fixture calibration

Calibrate each fixture independently with its lamp disabled or shutter closed.

## Before calibration

The DMX channels must be set before the calibration faders are shown. The calibration window directly drives the selected fixture, so FART needs to know where pan, tilt, dimmer, and optionally shutter are patched first.

1. Patch the exact fixture model and mode in the console or visualiser.
2. Enter the absolute DMX slots for pan coarse/fine, tilt coarse/fine, dimmer, and optional shutter.
3. Enter the fixture personality's real pan and tilt ranges. A nominal 540° pan personality normally maps to `-270` through `+270`.
4. Set reverse pan/tilt direction roughly correctly if known.
5. Stop the main FART output before starting calibration output.

## Calibration wizard workflow

1. Add a fixture and choose **calibration**, or select a fixture and click **Calibrate selected light**.
2. If the required DMX channels are incomplete, FART opens **DMX setup before calibration** first. Fill in pan coarse, tilt coarse, dimmer, and any fine/shutter channels.
3. Start calibration output with the dimmer kept low.
4. Select a known point, move the pan/tilt faders until the beam hits it, then click **Capture this point**.
5. Repeat for at least four points. Five or six points are recommended. Use points spread left/right, upstage/downstage, centre, and one raised point if possible.
6. Click **Solve and apply**.
7. Check the 3D preview and MA3/visualiser, then save settings.

The solver estimates the fixture optical-centre XYZ, pan-zero bearing, and tilt-zero elevation. Pan and tilt trims are reset to zero when the solve is applied. Reverse pan/tilt direction still needs to be correct before solving.

The software calculates the exact line from each fixture pivot to the marker. Correct pointing still depends on accurate channel maps, zero directions, personality angle ranges, and known calibration target positions.
