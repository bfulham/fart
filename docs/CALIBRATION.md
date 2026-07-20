# Fixture calibration

Calibrate each fixture independently with its lamp disabled or shutter closed.

1. Measure the fixture's pan/tilt pivot in the same coordinate system used by OpenFollow. Enter that as optical-centre X, Y, and Z.
2. Enter the fixture personality's real pan and tilt ranges. A nominal 540° pan personality normally maps to `-270` through `+270`.
3. Patch the exact fixture model and mode, then enter the absolute DMX slots for pan coarse/fine, tilt coarse/fine, dimmer, and optional shutter.
4. Assign the intended PSN marker to the selected light, then put that marker at a known point in front of the fixture. Start output while leaving **Arm all light dimmers** off.
5. Use **Set current bearing as pan zero** and **Set current elevation as tilt zero** when the fixture is physically aimed at that marker.
6. Move the marker stage left/right. Reverse pan direction if the fixture moves away from it.
7. Move the marker vertically or change its distance. Reverse tilt direction if the fixture moves away from it.
8. Verify at least six points: left, right, near, far, low, and high.
9. Check that no expected target causes a pan or tilt limit warning. Reposition the mechanical pan wrap if necessary.
10. Only arm dimmers after every fixture follows correctly with intensity disabled.

The software calculates the exact line from each fixture pivot to the marker. Correct pointing still depends on accurate fixture positions, channel maps, zero directions, and personality angle ranges.


## Calibration wizard workflow

1. Stop the main output.
2. Select the fixture in the Lights tab.
3. Click **Calibrate selected light** or choose calibration when adding a new fixture.
4. Start calibration output with the dimmer kept low.
5. Select a known point, move the pan/tilt faders until the beam hits it, then click **Capture this point**.
6. Repeat for at least four points. Five or six points are recommended. Use points spread left/right, upstage/downstage, centre, and one raised point if possible.
7. Click **Solve and apply**.
8. Check the 3D preview and MA3/visualiser, then save settings.

The solver estimates the fixture optical-centre XYZ, pan-zero bearing, and tilt-zero elevation. Pan and tilt trims are reset to zero when the solve is applied. Reverse pan/tilt direction still needs to be correct before solving.
