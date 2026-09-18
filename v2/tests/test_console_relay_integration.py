"""Full-stack proof of the "between a console and the lights" behaviour
that motivated the v2 redesign: real PSN in, real console-relay DMX in
(Art-Net and separately sACN), real DMX out (Art-Net and separately sACN),
through the actual Runner wiring real plugins together -- no mocks, no
calling run_cycle() directly.

test_runner_integration.py already proves plain tracking (PSN -> DMX out)
end to end. This file proves the console-relay path: auto-mode passthrough
of dimmer/beam channels via a console shadow patch, manual-mode full
passthrough, fader sourced from DMX in, and the safety fallbacks (tracking
loss, console loss, unarmed) -- and that DMX In and DMX Out can live on
completely independent universes/addresses (the whole point of the
fixture-type/patch redesign), not just different protocols.

Each test pairs DMX-in and DMX-out on *different* protocols (Art-Net in +
sACN out, or sACN in + Art-Net out) specifically so this test's own output
sniffer socket never has to share a port with the Runner's own input
receiver -- both Art-Net in and Art-Net out conventionally use UDP 6454,
and binding a second socket there to sniff outgoing unicast traffic would
race the Runner's real receiver for the same port, the same class of bug
this whole plugin architecture exists to rule out at the config level.
"""
import errno
import socket
import struct
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.config import FixtureConfig, FixtureType, Settings
from fart.engine import calculate_aim, resolve_fixture
from fart.plugins._artnet import ARTNET_PORT, build_artnet_dmx, parse_artnet_dmx
from fart.plugins._sacn import SACN_PORT, build_sacn_dmx, parse_sacn_dmx, sacn_multicast_group
from fart.runner import Runner


def multicast_sendto(sock, data, addr):
    """Some sandboxed CI network namespaces (seen on GitHub's hosted macOS
    runner) have no route to any multicast destination at all -- not even
    via loopback -- and every sendto() there fails immediately with
    ENETUNREACH/EHOSTUNREACH, unlike a real machine (dev or GitHub's
    Windows runner), where it's delivered over loopback normally. Treat
    that specific failure as "this environment can't run a real multicast
    test" and skip rather than fail, instead of forcing a workaround (an
    earlier attempt to pin the send to the loopback interface explicitly
    made things worse: it broke real delivery on a normal Mac outright)."""
    try:
        sock.sendto(data, addr)
    except OSError as exc:
        if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            raise unittest.SkipTest(f"real multicast send unavailable in this environment: {exc}") from exc
        raise


def psn_chunk(chunk_id, payload, sub=False):
    raw = (chunk_id & 0xFFFF) | ((len(payload) & 0x7FFF) << 16)
    if sub:
        raw |= 0x80000000
    return struct.pack("<I", raw) + payload


def build_psn_packet(marker_id, x, y, z):
    pos = psn_chunk(0x0000, struct.pack("<fff", x, y, z), sub=True)
    tracker = psn_chunk(marker_id, pos, sub=True)
    tracker_list = psn_chunk(0x0001, tracker, sub=True)
    return psn_chunk(0x6755, tracker_list, sub=True)


class ArtNetOutSniffer:
    """Binds the real Art-Net port to capture what the Runner's own
    ArtNetOutPlugin actually sends on the wire (it sends from an ephemeral
    port, so this never conflicts with it -- only with another *receiver*
    bound to 6454, which is why DMX-in in these tests is never Art-Net at
    the same time as this is in use)."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", ARTNET_PORT))
        self.sock.settimeout(0.3)

    def poll(self):
        try:
            data, _addr = self.sock.recvfrom(2048)
        except socket.timeout:
            return None
        return parse_artnet_dmx(data)

    def close(self):
        self.sock.close()


class SACNOutSniffer:
    def __init__(self, universe):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", SACN_PORT))
        for u in (universe if isinstance(universe, (list, tuple, set)) else [universe]):
            membership = socket.inet_aton(sacn_multicast_group(u)) + socket.inet_aton("0.0.0.0")
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.sock.settimeout(0.3)

    def poll(self):
        try:
            data, _addr = self.sock.recvfrom(2048)
        except socket.timeout:
            return None
        return parse_sacn_dmx(data)

    def close(self):
        self.sock.close()


class ConsoleArtNetSender:
    """Sends real Art-Net ArtDMX packets as if from a lighting console,
    to the Runner's real ArtNetInPlugin listening on 6454."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, universe, frame):
        self.sock.sendto(build_artnet_dmx(universe, frame), ("127.0.0.1", ARTNET_PORT))

    def close(self):
        self.sock.close()


class ConsoleSACNSender:
    """Sends real E1.31 sACN DATA packets as if from a lighting console,
    to whichever multicast group the Runner's real SACNInPlugin joined."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self.cid = uuid.uuid4().bytes
        self.seq = 0

    def send(self, universe, frame):
        packet = build_sacn_dmx(self.cid, "test console", self.seq, universe, frame)
        self.seq = (self.seq + 1) & 0xFF
        multicast_sendto(self.sock, packet, (sacn_multicast_group(universe), SACN_PORT))

    def close(self):
        self.sock.close()


class PSNSender:
    def __init__(self, multicast, port):
        self.multicast = multicast
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

    def send(self, marker_id, x, y, z):
        multicast_sendto(self.sock, build_psn_packet(marker_id, x, y, z), (self.multicast, self.port))

    def close(self):
        self.sock.close()


def pump_until(senders, sniffer, universe, predicate, timeout=6.0):
    """Repeatedly fire every sender, poll the sniffer, and decode frames
    for `universe` until predicate(frame_bytes) is true or time runs out.
    Returns the last decoded frame for that universe (or None)."""
    last = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for send in senders:
            send()
        parsed = sniffer.poll()
        if parsed is None:
            continue
        got_universe, frame = parsed
        if got_universe != universe:
            continue
        last = frame
        if predicate(frame):
            return last
    return last


def pan_tilt_from_frame(frame, resolved_fixture):
    pan_frac = ((frame[resolved_fixture.pan_coarse - 1] << 8) | frame[resolved_fixture.pan_fine - 1]) / 65535.0
    tilt_frac = ((frame[resolved_fixture.tilt_coarse - 1] << 8) | frame[resolved_fixture.tilt_fine - 1]) / 65535.0
    pan = resolved_fixture.pan_min + pan_frac * (resolved_fixture.pan_max - resolved_fixture.pan_min)
    tilt = resolved_fixture.tilt_min + tilt_frac * (resolved_fixture.tilt_max - resolved_fixture.tilt_min)
    return pan, tilt


def relay_fixture(**overrides):
    """A fixture with pan/tilt/dimmer/zoom/iris/focus on its type (offsets
    1-4 pan/tilt as usual, 5=dimmer, 6=zoom, 7=iris, 8=focus), output on
    universe 1, its console mode/marker control block on universe 10
    (channels 1/2 -- an arbitrary small block, nowhere near the real
    fixture's own footprint), and its console shadow feed (a live copy of
    the real fixture's channels 1-8) on universe 5 -- three genuinely
    different universes, proving DMX In and DMX Out no longer have to
    share one."""
    fixture_type = FixtureType(id="t", zoom=6, iris=7, focus=8)
    base = dict(
        marker_id=1, fixture_type_id="t", output_universe=1, output_start_address=1,
        x=0.0, y=-8.0, z=5.0,
        console_universe=10, console_mode_channel=1, console_marker_channel=2,
        shadow_universe=5, shadow_start_address=1,
    )
    base.update(overrides)
    fixture = FixtureConfig(**base)
    return fixture_type, fixture


class ConsoleRelayAutoModeTests(unittest.TestCase):
    """Auto mode: pan/tilt are computed from live tracking as usual, but
    the console's own dimmer and beam (zoom/iris/focus) channels -- read
    from a *separate* shadow-patch universe, not the mode/marker control
    universe -- are passed straight through untouched."""

    def test_artnet_in_sacn_out(self):
        fixture_type, fixture = relay_fixture()
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.21", 56610
        settings.psn_in.timeout_s = 1.0
        settings.dmx_in.active = "artnet"
        settings.dmx_out.active = "sacn"
        settings.dmx_out.sacn.universes = [1]

        sniffer = SACNOutSniffer(1)
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        console = ConsoleArtNetSender()
        marker_xyz = (5.0, 0.0, 4.0)
        mode_frame = bytearray(512)
        mode_frame[0] = 200  # mode channel (1 on console_universe): >=128 => auto
        mode_frame[1] = 0    # marker channel (2): 0 => use fixture default (marker 1)
        shadow_frame = bytearray(512)
        shadow_frame[4] = 180  # dimmer (offset 5): irrelevant -- FART always overwrites this in auto mode
        shadow_frame[5] = 111  # zoom (offset 6)
        shadow_frame[6] = 222  # iris (offset 7)
        shadow_frame[7] = 77   # focus (offset 8)

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        runner.fader.update(1.0)  # dimmer is always FART-computed in auto mode, never console-passed-through
        try:
            resolved = resolve_fixture(fixture, fixture_type)
            _b, _e, expected_pan, expected_tilt, _d = calculate_aim(resolved, *marker_xyz)

            def converged(frame):
                pan, tilt = pan_tilt_from_frame(frame, resolved)
                return abs(pan - expected_pan) <= 0.1 and abs(tilt - expected_tilt) <= 0.1 and frame[5] == 111

            frame = pump_until(
                [lambda: psn.send(1, *marker_xyz),
                 lambda: console.send(10, mode_frame),
                 lambda: console.send(5, shadow_frame)],
                sniffer, 1, converged,
            )
            self.assertIsNotNone(frame, "never received a decoded sACN out frame for universe 1")
            pan, tilt = pan_tilt_from_frame(frame, resolved)
            self.assertAlmostEqual(pan, expected_pan, delta=0.1)
            self.assertAlmostEqual(tilt, expected_tilt, delta=0.1)
            self.assertGreater(frame[4], 0, "dimmer is always FART-computed in auto mode (armed, fader > 0), never console-passed-through")
            self.assertEqual(frame[5], 111, "auto mode should pass the console's live zoom straight through from the shadow patch")
            self.assertEqual(frame[6], 222, "auto mode should pass the console's live iris straight through from the shadow patch")
            self.assertEqual(frame[7], 77, "auto mode should pass the console's live focus straight through from the shadow patch")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()
            console.close()

    def test_sacn_in_artnet_out(self):
        fixture_type, fixture = relay_fixture()
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.22", 56611
        settings.psn_in.timeout_s = 1.0
        settings.dmx_in.active = "sacn"
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"

        sniffer = ArtNetOutSniffer()
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        console = ConsoleSACNSender()
        marker_xyz = (-3.0, 2.0, 1.5)
        mode_frame = bytearray(512)
        mode_frame[0] = 255
        mode_frame[1] = 0
        shadow_frame = bytearray(512)
        shadow_frame[4] = 90

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        runner.fader.update(1.0)  # dimmer is always FART-computed in auto mode, never console-passed-through
        try:
            resolved = resolve_fixture(fixture, fixture_type)
            _b, _e, expected_pan, expected_tilt, _d = calculate_aim(resolved, *marker_xyz)

            def converged(frame):
                pan, tilt = pan_tilt_from_frame(frame, resolved)
                return abs(pan - expected_pan) <= 0.1 and abs(tilt - expected_tilt) <= 0.1

            frame = pump_until(
                [lambda: psn.send(1, *marker_xyz),
                 lambda: console.send(10, mode_frame),
                 lambda: console.send(5, shadow_frame)],
                sniffer, 1, converged,
            )
            self.assertIsNotNone(frame, "never received a decoded Art-Net out frame for universe 1")
            pan, tilt = pan_tilt_from_frame(frame, resolved)
            self.assertAlmostEqual(pan, expected_pan, delta=0.1)
            self.assertAlmostEqual(tilt, expected_tilt, delta=0.1)
            self.assertGreater(frame[4], 0, "dimmer is always FART-computed in auto mode (armed, fader > 0), never console-passed-through")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()
            console.close()


class ConsoleRelayManualModeTests(unittest.TestCase):
    """Manual mode: FART must not touch this fixture's channels at all --
    the shadow patch's raw bytes go out byte-for-byte, including pan/tilt,
    which will *not* match calculate_aim if the console is driving pan/tilt
    itself (proving this isn't accidentally computing and overwriting it)."""

    def test_artnet_in_sacn_out_full_passthrough(self):
        fixture_type, fixture = relay_fixture()
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.23", 56612
        settings.psn_in.timeout_s = 1.0
        settings.dmx_in.active = "artnet"
        settings.dmx_out.active = "sacn"
        settings.dmx_out.sacn.universes = [1]

        sniffer = SACNOutSniffer(1)
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        console = ConsoleArtNetSender()
        mode_frame = bytearray(512)
        mode_frame[0] = 10  # mode channel < 128 => manual
        shadow_frame = bytearray(512)
        shadow_frame[0] = 111  # pan coarse -- deliberately NOT what calculate_aim would produce
        shadow_frame[1] = 222  # pan fine
        shadow_frame[4] = 200  # dimmer

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        try:
            frame = pump_until(
                [lambda: psn.send(1, 5.0, 0.0, 4.0),
                 lambda: console.send(10, mode_frame),
                 lambda: console.send(5, shadow_frame)],
                sniffer, 1, lambda f: f[0] == 111 and f[1] == 222,
            )
            self.assertIsNotNone(frame, "never received a decoded sACN out frame for universe 1")
            self.assertEqual(frame[0], 111, "manual mode must pass the shadow patch's raw pan-coarse byte through untouched")
            self.assertEqual(frame[1], 222, "manual mode must pass the shadow patch's raw pan-fine byte through untouched")
            self.assertEqual(frame[4], 200, "manual mode must pass the shadow patch's raw dimmer byte through untouched")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()
            console.close()


class FaderFromDMXInTests(unittest.TestCase):
    """fader.source == "dmx_in": the operator's manual fader is replaced by
    a live channel value read off the active DMX-in bus (no console-relay
    fixtures involved at all -- this is the whole-rig master intensity
    fader, separate from per-fixture console relay)."""

    def test_artnet_in_channel_drives_output_intensity(self):
        fixture_type = FixtureType(id="t")
        fixture = FixtureConfig(marker_id=1, fixture_type_id="t", output_universe=1, x=0.0, y=-8.0, z=5.0)
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.24", 56613
        settings.psn_in.timeout_s = 1.0
        settings.dmx_in.active = "artnet"
        settings.fader.source = "dmx_in"
        settings.fader.channel = 20
        settings.dmx_out.active = "sacn"
        settings.dmx_out.sacn.universes = [1]

        sniffer = SACNOutSniffer(1)
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        console = ConsoleArtNetSender()
        # fader.channel reads from dmx_in.active's *own configured universe*
        # (settings.dmx_in.artnet.universe) via ExternalInputBus, independent
        # of any fixture's output_universe or console/shadow patches.
        fader_universe = settings.dmx_in.artnet.universe
        console_frame = bytearray(512)
        console_frame[19] = 128  # channel 20 -> fader ~= 128/255 = 0.502

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        try:
            frame = pump_until(
                [lambda: psn.send(1, 5.0, 0.0, 4.0), lambda: console.send(fader_universe, console_frame)],
                sniffer, 1, lambda f: f[4] not in (0, 255),
            )
            self.assertIsNotNone(frame, "never received a decoded sACN out frame for universe 1")
            self.assertAlmostEqual(frame[4] / 255.0, 128 / 255.0, delta=0.02,
                                    msg="output dimmer should track the live DMX-in fader channel, not be full or zero")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()
            console.close()


class SafetyFallbackTests(unittest.TestCase):
    """Real Runner + real sockets versions of the blackout safety rules
    that test_engine.py already checks against run_cycle() directly --
    proving the same behaviour survives the actual plugin/thread wiring,
    not just the pure function."""

    def _fixture(self, **overrides):
        fixture_type = FixtureType(id="t")
        base = dict(marker_id=1, fixture_type_id="t", output_universe=1, x=0.0, y=-8.0, z=5.0)
        base.update(overrides)
        return fixture_type, FixtureConfig(**base)

    def test_tracking_loss_blacks_out_dimmer(self):
        fixture_type, fixture = self._fixture()
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.25", 56614
        settings.psn_in.timeout_s = 0.3
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"

        sniffer = ArtNetOutSniffer()
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        runner.fader.update(1.0)  # manual fader source: FaderState defaults to 0.0 (dark)
        try:
            frame = pump_until([lambda: psn.send(1, 5.0, 0.0, 4.0)], sniffer, 1, lambda f: f[4] > 0)
            self.assertIsNotNone(frame)
            self.assertGreater(frame[4], 0, "dimmer should be lit while tracking is live and armed")

            # Stop sending PSN entirely and wait past timeout_s: tracking
            # goes stale, and the default on_tracking_loss ("Blackout")
            # must force the dimmer to 0 even though nothing else changed.
            frame = pump_until([], sniffer, 1, lambda f: f[4] == 0, timeout=3.0)
            self.assertIsNotNone(frame)
            self.assertEqual(frame[4], 0, "dimmer must black out once tracking goes stale")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()

    def test_unarmed_blacks_out_dimmer_but_keeps_tracking(self):
        fixture_type, fixture = self._fixture()
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.26", 56615
        settings.psn_in.timeout_s = 1.0
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"

        sniffer = ArtNetOutSniffer()
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        marker_xyz = (5.0, 0.0, 4.0)

        runner = Runner()
        runner.start(settings)
        runner.armed = False  # never armed
        try:
            resolved = resolve_fixture(fixture, fixture_type)
            _b, _e, expected_pan, expected_tilt, _d = calculate_aim(resolved, *marker_xyz)

            def converged(frame):
                pan, tilt = pan_tilt_from_frame(frame, resolved)
                return abs(pan - expected_pan) <= 0.1 and abs(tilt - expected_tilt) <= 0.1

            frame = pump_until([lambda: psn.send(1, *marker_xyz)], sniffer, 1, converged)
            self.assertIsNotNone(frame)
            pan, tilt = pan_tilt_from_frame(frame, resolved)
            self.assertAlmostEqual(pan, expected_pan, delta=0.1,
                                    msg="unarmed must still compute pan/tilt so the light is pre-aimed")
            self.assertAlmostEqual(tilt, expected_tilt, delta=0.1)
            self.assertEqual(frame[4], 0, "unarmed must force the dimmer to 0 regardless of tracking")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()

    def test_console_loss_during_manual_falls_back_to_tracking_and_blackout(self):
        """If the console goes away while a fixture was in manual (full
        passthrough) mode, holding the last raw frame forever would leave a
        light frozen wherever the console last pointed it -- with no
        tracking and no operator control. The engine instead falls back to
        computing pan/tilt from live tracking again and forces the dimmer
        off (default on_console_loss = "Blackout"), rather than a silent
        frozen light."""
        fixture_type, fixture = relay_fixture()
        settings = Settings(fixture_types=[fixture_type], fixtures=[fixture])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.27", 56616
        settings.psn_in.timeout_s = 0.3
        settings.dmx_in.active = "artnet"
        settings.dmx_out.active = "sacn"
        settings.dmx_out.sacn.universes = [1]

        sniffer = SACNOutSniffer(1)
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        console = ConsoleArtNetSender()
        marker_xyz = (5.0, 0.0, 4.0)
        mode_frame = bytearray(512)
        mode_frame[0] = 10  # manual
        shadow_frame = bytearray(512)
        shadow_frame[0] = 111
        shadow_frame[1] = 222
        shadow_frame[4] = 200

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        try:
            frame = pump_until(
                [lambda: psn.send(1, *marker_xyz),
                 lambda: console.send(10, mode_frame),
                 lambda: console.send(5, shadow_frame)],
                sniffer, 1, lambda f: f[0] == 111 and f[1] == 222,
            )
            self.assertIsNotNone(frame, "should have seen manual full passthrough first")

            # Now stop the console entirely (keep tracking alive) and wait
            # past timeout_s for the mode-channel frame to go stale.
            resolved = resolve_fixture(fixture, fixture_type)
            _b, _e, expected_pan, expected_tilt, _d = calculate_aim(resolved, *marker_xyz)

            def fell_back(frame):
                pan, tilt = pan_tilt_from_frame(frame, resolved)
                return frame[4] == 0 and abs(pan - expected_pan) <= 0.1 and abs(tilt - expected_tilt) <= 0.1

            frame = pump_until([lambda: psn.send(1, *marker_xyz)], sniffer, 1, fell_back, timeout=4.0)
            self.assertIsNotNone(frame)
            pan, tilt = pan_tilt_from_frame(frame, resolved)
            self.assertAlmostEqual(pan, expected_pan, delta=0.1,
                                    msg="losing the console must fall back to computed tracking, not a frozen frame")
            self.assertEqual(frame[4], 0, "losing the console must force the dimmer off by default")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()
            console.close()


class SACNInMultiUniverseTests(unittest.TestCase):
    """SACNInPlugin must join a multicast group for every universe that
    actually needs sACN DMX-in data (engine.dmx_in_universes_needed), not
    just the single universe named on the DMX In tab -- otherwise a
    fixture whose console relay lives on a different sACN universe would
    silently and permanently read as console-lost, even though a real
    console is genuinely sending it on the wire. (This was a real,
    confirmed bug found while writing this test suite, fixed in
    sacn_in.py -- Art-Net-in never had this problem since it accepts every
    incoming universe for free, with no per-universe join needed.)"""

    def test_fixtures_on_different_sacn_universes_both_get_console_relay(self):
        type_a = FixtureType(id="ta")
        type_b = FixtureType(id="tb")
        fixture_a = FixtureConfig(
            name="A", marker_id=1, fixture_type_id="ta", output_universe=1, x=0.0, y=-8.0, z=5.0,
            console_universe=1, console_mode_channel=10, console_marker_channel=0,
        )
        fixture_b = FixtureConfig(
            name="B", marker_id=2, fixture_type_id="tb", output_universe=2, x=0.0, y=-8.0, z=5.0,
            console_universe=2, console_mode_channel=10, console_marker_channel=0,
        )
        settings = Settings(fixture_types=[type_a, type_b], fixtures=[fixture_a, fixture_b])
        settings.psn_in.multicast, settings.psn_in.port = "236.10.10.28", 56617
        settings.psn_in.timeout_s = 1.0
        settings.dmx_in.active = "sacn"
        settings.dmx_in.sacn.universe = 1  # only referenced directly for the master fader
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"

        sniffer = ArtNetOutSniffer()
        psn = PSNSender(settings.psn_in.multicast, settings.psn_in.port)
        console = ConsoleSACNSender()
        auto_frame = bytearray(512)
        auto_frame[9] = 200  # auto, non-stale -- lets the (forced by armed+fader) dimmer through

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        runner.fader.update(1.0)  # dimmer is always FART-computed in auto mode now, never console-passed-through
        try:
            def senders():
                psn.send(1, 5.0, 0.0, 4.0)
                psn.send(2, 5.0, 0.0, 4.0)
                console.send(1, auto_frame)  # fixture_a's console_universe
                console.send(2, auto_frame)  # fixture_b's console_universe -- a *different* universe

            frame_a = frame_b = None
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                senders()
                parsed = sniffer.poll()
                if parsed is None:
                    continue
                universe, frame = parsed
                if universe == 1:
                    frame_a = frame
                elif universe == 2:
                    frame_b = frame
                if frame_a is not None and frame_b is not None and frame_a[4] > 0 and frame_b[4] > 0:
                    break

            self.assertIsNotNone(frame_a)
            self.assertIsNotNone(frame_b)
            self.assertGreater(frame_a[4], 0,
                                "fixture A's console relay (universe 1) should not read as console-lost")
            self.assertGreater(frame_b[4], 0,
                                "fixture B's console relay (universe 2) should also not read as console-lost -- "
                                "SACNInPlugin must join a group per universe actually needed, not just dmx_in.sacn.universe")
        finally:
            runner.stop()
            sniffer.close()
            psn.close()
            console.close()


if __name__ == "__main__":
    unittest.main()
