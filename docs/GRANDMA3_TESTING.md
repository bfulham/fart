# Testing FART in grandMA3 onPC

Use a dedicated Art-Net universe so FART cannot interfere with normal show data.

## Example layout

- FART Art-Net universe: `99`
- grandMA3 local universe: `100`
- Test fixture patch: `100.101`
- PSN MArker fixture: a different MA universe, such as `2.001`

In **Menu → DMX Protocols → Art-Net**, enable input and create a row mapping Art-Net absolute universe `99` to local universe `100`. Use priority merge for 16-bit pan and tilt.

Patch the exact real fixture personality and mode at `100.101`. Enter absolute channel numbers in FART. The fixture's start address is not automatically its pan channel. For example, a pan attribute at fixture offset 18 on a fixture starting at 101 uses absolute channel `118`.

Keep the PSN MArker on an MA universe that is not receiving FART Art-Net. Otherwise the full incoming frame can zero the marker's XYZ attributes and move it to `0,0,0`.

Compare the raw MA DMX Sheet with ArtNetominator. If the values match but the visualised fixture behaves incorrectly, check the fixture personality, mode, start address, channel offsets, shutter range, and coarse/fine order.
