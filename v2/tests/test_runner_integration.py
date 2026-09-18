"""End-to-end proof: real PSN packets in, real Art-Net (and separately
sACN) frames out, through the actual Runner wiring real plugins to the
real engine -- no mocks anywhere in this path.
"""
import errno
import socket
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fart.config import FixtureConfig, FixtureType, Settings
from fart.engine import calculate_aim, resolve_fixture
from fart.plugins._artnet import parse_artnet_dmx
from fart.runner import Runner


def wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


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


class RunnerEndToEndTests(unittest.TestCase):
    def _base_settings(self):
        settings = Settings()
        settings.psn_in.multicast = "236.10.10.12"
        settings.psn_in.port = 56588
        settings.psn_in.timeout_s = 2.0
        settings.psn_in.smoothing = 0.0
        settings.refresh_hz = 30
        fixture_type = FixtureType(id="t")
        fixture = FixtureConfig(marker_id=1, output_universe=0, x=0.0, y=-8.0, z=5.0, fixture_type_id="t")
        settings.fixture_types = [fixture_type]
        settings.fixtures = [fixture]
        return settings, resolve_fixture(fixture, fixture_type)

    def test_psn_to_artnet_out_matches_calculate_aim(self):
        settings, fixture = self._base_settings()
        settings.dmx_out.active = "artnet"
        settings.dmx_out.artnet.target_ip = "127.0.0.1"

        sniffer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sniffer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sniffer.bind(("", 6454))
        sniffer.settimeout(0.5)

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        try:
            marker_pos = (5.0, 0.0, 4.0)
            psn_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            psn_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            packet = build_psn_packet(1, *marker_pos)

            _b, _e, expected_pan, expected_tilt, _d = calculate_aim(fixture, *marker_pos)

            # Keep sending PSN and reading frames until the decoded pan
            # converges on the expected value: the first frame(s) received
            # can predate the runner's tracker bank actually being updated,
            # so accepting only the first frame received would just measure
            # who won a startup race, not whether the pipeline is correct.
            actual_pan = actual_tilt = None
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                multicast_sendto(psn_sock, packet, (settings.psn_in.multicast, settings.psn_in.port))
                try:
                    data, _addr = sniffer.recvfrom(2048)
                except socket.timeout:
                    continue
                parsed = parse_artnet_dmx(data)
                if not (parsed and parsed[0] == 0):
                    continue
                received_frame = parsed[1]
                pan_frac = ((received_frame[fixture.pan_coarse - 1] << 8) | received_frame[fixture.pan_fine - 1]) / 65535.0
                tilt_frac = ((received_frame[fixture.tilt_coarse - 1] << 8) | received_frame[fixture.tilt_fine - 1]) / 65535.0
                actual_pan = fixture.pan_min + pan_frac * (fixture.pan_max - fixture.pan_min)
                actual_tilt = fixture.tilt_min + tilt_frac * (fixture.tilt_max - fixture.tilt_min)
                if abs(actual_pan - expected_pan) <= 0.1 and abs(actual_tilt - expected_tilt) <= 0.1:
                    break
            psn_sock.close()

            self.assertIsNotNone(actual_pan, "never received an Art-Net frame for universe 0")
            self.assertAlmostEqual(actual_pan, expected_pan, delta=0.1)
            self.assertAlmostEqual(actual_tilt, expected_tilt, delta=0.1)
        finally:
            runner.stop()
            sniffer.close()

    def test_psn_to_sacn_out(self):
        settings, fixture = self._base_settings()
        settings.dmx_out.active = "sacn"
        settings.dmx_out.sacn.universes = [1]
        settings.fixtures[0].output_universe = 1

        from fart.plugins._sacn import parse_sacn_dmx, sacn_multicast_group

        sniffer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sniffer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sniffer.bind(("", 5568))
        membership = socket.inet_aton(sacn_multicast_group(1)) + socket.inet_aton("0.0.0.0")
        sniffer.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sniffer.settimeout(0.5)

        runner = Runner()
        runner.start(settings)
        runner.armed = True
        try:
            psn_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            psn_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            packet = build_psn_packet(1, 5.0, 0.0, 4.0)

            received = None
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and received is None:
                multicast_sendto(psn_sock, packet, (settings.psn_in.multicast, settings.psn_in.port))
                try:
                    data, _addr = sniffer.recvfrom(2048)
                except socket.timeout:
                    continue
                parsed = parse_sacn_dmx(data)
                if parsed and parsed[0] == 1:
                    received = parsed[1]
            psn_sock.close()

            self.assertIsNotNone(received, "never received a real sACN frame for universe 1")
            self.assertGreater(received[fixture.pan_coarse - 1] + received[fixture.pan_fine - 1], 0,
                                "pan channel should be nonzero for a marker off to one side")
        finally:
            runner.stop()
            sniffer.close()


if __name__ == "__main__":
    unittest.main()
