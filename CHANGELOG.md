# FART v2 changelog

## 2.2.1

- **Shadow patch and console mode/marker control now have explicit tickboxes** (Fixtures tab) instead of "universe 0 = off" / "mode channel 0 = disabled". Universe 0 is a real Art-Net universe, so a shadow feed or console block on universe 0 could not be enabled before. Existing files and patches keep working: an unset switch is inferred once from the old values (shadow universe > 0, a mode or marker channel set), and from then on the saved tickbox is what counts. Console control needs at least one of the mode and marker channels; 0 means that channel is not used. sACN has no universe 0, so use a universe of 1 or more there.
- Full passthrough (mode < 128) says why it is outputting nothing in the Operator table's State column (shadow patch not enabled, footprint 0, still waiting for the feed), and Start logs a warning for a fixture with console control but no shadow patch.

## 2.2.0

Changes from field testing. Breaking changes first.

**Breaking**
- The **Arm all light dimmers** control is gone, completely (`Runner.armed`,
  the `armed` argument of `run_cycle`, the Operator checkbox, the "DIMMER
  LOCKED" states). Start is the only gate, and the manual dimmer fader
  always starts at 0.
- `Settings.fader` (a master dimmer read from a DMX-in channel) and
  `Settings.zoom_mode` are replaced by `use_dmx_in`, `dimmer_mode`
  (`manual` | `dmx_in`) and `beam_mode` (`manual` | `auto_size` | `dmx`).
  Existing files load: `zoom_mode` "Auto beam size" becomes `auto_size`; the
  DMX master-fader source is dropped (dimmer falls back to Manual).

**New**
- **Use DMX in** switch (Operator tab): unticked is FART-only control, the
  same as every fixture's DMX-in addresses being 0.
- **Dimmer mode** (Manual / DMX in) and **Beam mode** (Manual / Auto size /
  DMX passthrough). In DMX in, the dimmer passes through from the fixture's
  shadow feed; tracking-loss, console-loss and marker-change blackouts still
  force it to 0. Fixtures with no shadow feed fall back to the faders.
- Every fader with no effect is greyed out.
- DMX in has its own timeout (`dmx_in.timeout_s`, default 3 s) instead of
  reusing PSN's 0.5 s tracking timeout. A console sending under 1 fps used
  to read as lost most cycles and made the dimmer and mode/marker channels
  flicker.
- sACN in locks onto the highest-priority source per universe (first seen
  wins among equals, failover after 2.5 s), drops duplicate and late packets,
  honours stream termination and ignores non-zero start codes, so two sources
  on one universe no longer alternate frames.
- Operator tab shows per-universe DMX-in packet rate and last-packet age, and
  a distinct "DMX-IN LOST" state.
- PSN In tab shows a live table of every tracker received -- position, speed
  (sent and derived), orientation, acceleration, target, status, name, rate,
  age and which fixtures use it -- whether or not any fixture uses it.
- DMX In / DMX Out channel grids: hovering a cell shows "Channel N = value"
  instead of a row/column formula.
- Calibration wizard: step buttons (1 / 0.1 / 0.01 degrees), exact numeric pan
  and tilt entry, arrow-key nudging (Shift = a tenth of the step, Ctrl = ten
  times), and Shift for fine drag on the pan dial and tilt arc.

**Fixed**
- The PSN decoder stopped reading a tracker's fields at the position chunk;
  it now reads them all.
