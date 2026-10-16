# Testing FART in grandMA3 onPC

Use a dedicated Art-Net universe so FART cannot interfere with normal show data.

## Example layout

- FART Art-Net universe: `99`
- grandMA3 local universe: `100`
- Test fixture patch: `100.101`
- PSN marker fixture: a different MA universe, such as `2.001`

In **Menu → DMX Protocols → Art-Net**, enable input and create a row mapping Art-Net absolute universe `99` to local universe `100`. Use priority merge for 16-bit pan and tilt.

Patch the exact real fixture personality and mode at `100.101`, and set the matching **DMX Out patch** (output universe and start address) on the fixture in FART, with a fixture type whose channel offsets match that mode. A GDTF-backed type does this for you; pick the same DMX mode on the fixture that you patched in MA.

Keep the PSN marker fixture on an MA universe that is not receiving FART's Art-Net output. Otherwise the full incoming frame can zero the marker's XYZ attributes and move it to `0,0,0`.

Compare the raw MA DMX Sheet with ArtNetominator (or another Art-Net packet monitor). If the values match but the visualised fixture behaves incorrectly, check the fixture personality, mode, start address, channel offsets, shutter range, and coarse/fine channel order.

## Testing console control

To exercise the [shadow patch and mode/marker control](../README.md#console-control-shadow-patch-and-modemarker) from onPC, send a second universe from MA to FART (on the **DMX In** tab's protocol) that carries the real fixture's channels, and a small block for the mode and marker channels. Enable the shadow patch and the mode/marker control on the fixture and point them at those universes. The DMX In tab's live monitor shows whether FART is receiving what MA is sending, and the Operator tab's DMX-in line shows each universe's packet rate.
