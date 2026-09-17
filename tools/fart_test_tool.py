#!/usr/bin/env python3
"""Standalone test harness for FART — no OpenFollow and no real fixture needed.

Three things you can do with it:

  send-psn        Sends synthetic OpenFollow-style PSN tracker positions, so
                   FART has something to track without OpenFollow running.
  monitor-artnet  Listens for FART's own Art-Net output and decodes the
                   requested channels back into human-readable values, so you
                   can see what FART is actually sending without a real
                   fixture or DMX node.
  verify          Runs both at once: sends a scripted marker trajectory and
                   independently computes the pan/tilt FART *should* produce
                   for it, then compares that against what FART *actually*
                   sent back over Art-Net. Reports PASS/FAIL with the
                   observed error, instead of requiring you to eyeball it.
  send-console    Sends a synthetic "lighting console" Art-Net frame driving
                   FART's live console-control mode/marker-select channels,
                   so that feature can be exercised without a real console.

Deliberately dependency-free (standard library only, no tkinter/pyserial/
sacn/import of fart.py) so it can run on a different, lighter machine than
FART itself — for example a laptop next to the console, testing a FART
instance running on the actual show PC.

This mirrors, but does not import, fart.py's wire formats: PSN chunk/packet
layout (see PSNReceiver._decode / _chunks in fart.py), the Art-Net ArtDMX
header layout (see parse_artnet_dmx() in fart.py), and the 16-bit DMX
fraction mapping (see dmx16() / calculate_aim() in fart.py). If FART's wire
format ever changes, this file needs updating to match by hand.
"""
from __future__ import annotations

import argparse
import math
import signal
import socket
import struct
import sys
import threading
import time

ARTNET_PORT = 6454

# Set by an explicit SIGINT handler (installed in main()) rather than relying
# solely on the implicit KeyboardInterrupt conversion, which is not always
# delivered promptly to the main thread when a receiver loop is blocked in a
# socket call alongside a background sender thread. All loops below check
# this instead of using `while True`.
_STOP = threading.Event()


def _handle_sigint(_signum, _frame):
    _STOP.set()
PSN_DATA_PACKET = 0x6755
PSN_DATA_TRACKER_LIST = 0x0001
PSN_DATA_TRACKER_POS = 0x0000


# ---------------------------------------------------------------------------
# Shared wire-format helpers
# ---------------------------------------------------------------------------

def psn_chunk(chunk_id, payload, sub=False):
    """Build one PSN chunk header + payload. Mirrors _chunks() in fart.py."""
    raw = (chunk_id & 0xFFFF) | ((len(payload) & 0x7FFF) << 16)
    if sub:
        raw |= 0x80000000
    return struct.pack("<I", raw) + payload


def build_psn_packet(markers):
    """markers: iterable of (marker_id, x, y, z). Returns one PSN data packet.

    Matches what OpenFollow/pypsn sends and what fart.py's PSNReceiver
    decodes: a DATA_PACKET containing one DATA_TRACKER_LIST, containing one
    chunk per tracker (keyed by marker id), each containing a 12-byte
    DATA_TRACKER_POS leaf with the marker's float32 XYZ.
    """
    tracker_chunks = b""
    for marker_id, x, y, z in markers:
        pos = psn_chunk(PSN_DATA_TRACKER_POS, struct.pack("<fff", x, y, z), sub=True)
        tracker_chunks += psn_chunk(int(marker_id), pos, sub=True)
    tracker_list = psn_chunk(PSN_DATA_TRACKER_LIST, tracker_chunks, sub=True)
    return psn_chunk(PSN_DATA_PACKET, tracker_list, sub=True)


def parse_artnet_dmx(data):
    """Parse an Art-Net ArtDMX UDP packet. Returns (universe, dmx_bytes) or
    None. Mirrors parse_artnet_dmx() in fart.py exactly."""
    if len(data) < 18 or data[:8] != b"Art-Net\x00":
        return None
    if struct.unpack_from("<H", data, 8)[0] != 0x5000:
        return None
    universe = struct.unpack_from("<H", data, 14)[0]
    length = struct.unpack_from(">H", data, 16)[0]
    if 18 + length > len(data):
        return None
    return universe, bytes(data[18:18 + length])


def build_artnet_dmx(universe, dmx_bytes):
    """Build one ArtDMX packet. Mirrors ArtNet.send() in fart.py."""
    frame = bytes(dmx_bytes) + bytes(512 - len(dmx_bytes))
    header = (
        b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
        + bytes((0, 0)) + struct.pack("<H", int(universe)) + struct.pack(">H", 512)
    )
    return header + frame


def dmx16_fraction(coarse, fine):
    return ((coarse << 8) | fine) / 65535.0


def wrap180(v):
    return (v + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------
# Marker motion patterns, shared by send-psn and verify
# ---------------------------------------------------------------------------

def marker_position(pattern, t, cx, cy, cz, radius, speed):
    """Returns (x, y, z) for `pattern` at time t seconds since start."""
    if pattern == "static":
        return cx, cy, cz
    if pattern == "circle":
        angle = t * speed
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle), cz
    if pattern == "line":
        return cx + radius * math.sin(t * speed), cy, cz
    raise ValueError(f"Unknown pattern: {pattern!r} (expected static, circle, or line)")


def add_marker_pattern_args(parser, default_pattern="circle"):
    parser.add_argument("--marker-id", type=int, default=1, help="PSN marker ID to send (default: 1)")
    parser.add_argument("--pattern", choices=["static", "circle", "line"], default=default_pattern)
    parser.add_argument("--x", type=float, default=0.0, help="Pattern centre X (default: 0.0)")
    parser.add_argument("--y", type=float, default=0.0, help="Pattern centre Y (default: 0.0)")
    parser.add_argument("--z", type=float, default=1.7, help="Height (default: 1.7, roughly head height)")
    parser.add_argument("--radius", type=float, default=2.0, help="circle/line amplitude in metres (default: 2.0)")
    parser.add_argument("--speed", type=float, default=0.2, help="angular/sweep speed, radians/s (default: 0.2 — slow, so FART's smoothing has time to settle)")
    parser.add_argument("--rate", type=float, default=30.0, help="packets per second (default: 30, matching FART's default refresh rate)")


def add_psn_network_args(parser):
    parser.add_argument("--group", default="236.10.10.10", help="PSN multicast group (default: FART's default, 236.10.10.10)")
    parser.add_argument("--port", type=int, default=56565, help="PSN UDP port (default: FART's default, 56565)")


def add_fixture_args(parser):
    """Defaults exactly match FixtureConfig's own defaults in fart.py, so a
    freshly-added, uncalibrated FART light can be tested with no flags."""
    parser.add_argument("--fixture-x", type=float, default=0.0)
    parser.add_argument("--fixture-y", type=float, default=-8.0)
    parser.add_argument("--fixture-z", type=float, default=5.0)
    parser.add_argument("--pan-zero-bearing", type=float, default=0.0)
    parser.add_argument("--tilt-zero-elevation", type=float, default=0.0)
    parser.add_argument("--pan-direction", type=int, choices=[-1, 1], default=1)
    parser.add_argument("--tilt-direction", type=int, choices=[-1, 1], default=-1)
    parser.add_argument("--pan-offset", type=float, default=0.0)
    parser.add_argument("--tilt-offset", type=float, default=0.0)
    parser.add_argument("--pan-min", type=float, default=-270.0)
    parser.add_argument("--pan-max", type=float, default=270.0)
    parser.add_argument("--tilt-min", type=float, default=-135.0)
    parser.add_argument("--tilt-max", type=float, default=135.0)
    parser.add_argument("--pan-coarse", type=int, default=1)
    parser.add_argument("--pan-fine", type=int, default=2)
    parser.add_argument("--tilt-coarse", type=int, default=3)
    parser.add_argument("--tilt-fine", type=int, default=4)


def expected_pan_tilt(marker_xyz, args):
    """Reimplements calculate_aim()'s geometry (not its previous-pan-aware
    multi-turn tie-break, which needs FART's own running state) closely
    enough to sanity-check FART's output: independently computes what pan/
    tilt a fixture with this calibration should produce for this marker
    position. See calculate_aim() in fart.py for the authoritative version.
    """
    dx = marker_xyz[0] - args.fixture_x
    dy = marker_xyz[1] - args.fixture_y
    dz = marker_xyz[2] - args.fixture_z
    bearing = math.degrees(math.atan2(dx, dy))
    elevation = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    base_pan = wrap180(bearing - args.pan_zero_bearing) * args.pan_direction + args.pan_offset
    tilt = (elevation - args.tilt_zero_elevation) * args.tilt_direction + args.tilt_offset
    candidates = [base_pan + 360 * k for k in range(-3, 4)]
    valid = [p for p in candidates if args.pan_min <= p <= args.pan_max]
    reference = (args.pan_min + args.pan_max) / 2.0
    pan = min(valid or candidates, key=lambda p: abs(p - reference))
    pan = max(args.pan_min, min(args.pan_max, pan))
    tilt = max(args.tilt_min, min(args.tilt_max, tilt))
    return pan, tilt


def decoded_pan_tilt(dmx, args):
    """Inverts write_fixture_to_frame()'s pan/tilt encoding: DMX bytes -> angle."""
    if args.pan_coarse > len(dmx) or args.tilt_coarse > len(dmx):
        return None
    pan_fine = dmx[args.pan_fine - 1] if args.pan_fine and args.pan_fine <= len(dmx) else 0
    tilt_fine = dmx[args.tilt_fine - 1] if args.tilt_fine and args.tilt_fine <= len(dmx) else 0
    pan_frac = dmx16_fraction(dmx[args.pan_coarse - 1], pan_fine)
    tilt_frac = dmx16_fraction(dmx[args.tilt_coarse - 1], tilt_fine)
    pan = args.pan_min + pan_frac * (args.pan_max - args.pan_min)
    tilt = args.tilt_min + tilt_frac * (args.tilt_max - args.tilt_min)
    return pan, tilt


# ---------------------------------------------------------------------------
# send-psn
# ---------------------------------------------------------------------------

def cmd_send_psn(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
    dest = (args.group, args.port)
    interval = 1.0 / args.rate
    start = time.monotonic()
    count = 0
    print(f"Sending PSN marker {args.marker_id} ({args.pattern}) to {args.group}:{args.port} "
          f"at {args.rate:g} Hz. In FART: Operator tab -> PSN multicast {args.group}, "
          f"UDP port {args.port}. Ctrl+C to stop.")
    try:
        while not _STOP.is_set():
            t = time.monotonic() - start
            x, y, z = marker_position(args.pattern, t, args.x, args.y, args.z, args.radius, args.speed)
            sock.sendto(build_psn_packet([(args.marker_id, x, y, z)]), dest)
            count += 1
            if count % max(1, int(args.rate)) == 0:
                print(f"  t={t:6.1f}s  marker {args.marker_id}: x={x:+.3f} y={y:+.3f} z={z:+.3f}  "
                      f"({count} packets sent)")
            time.sleep(interval)
        print(f"\nStopped after {count} packets.")
    except KeyboardInterrupt:
        print(f"\nStopped after {count} packets.")
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# monitor-artnet
# ---------------------------------------------------------------------------

def parse_channel_ranges(spec):
    channels = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            channels.extend(range(int(lo), int(hi) + 1))
        else:
            channels.append(int(part))
    return channels


def cmd_monitor_artnet(args):
    channels = parse_channel_ranges(args.channels) if args.channels else None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", ARTNET_PORT))
    sock.settimeout(0.5)
    print(f"Listening for Art-Net on UDP {ARTNET_PORT}, universe {args.universe}. "
          f"In FART: Setup: I/O -> Output = Art-Net, target IP = this machine's address "
          f"(127.0.0.1 if running on the same machine). Ctrl+C to stop.")
    last_frame = None
    last_print = 0.0
    try:
        while not _STOP.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            parsed = parse_artnet_dmx(data)
            if not parsed:
                continue
            universe, dmx = parsed
            if universe != args.universe:
                continue
            now = time.monotonic()
            if dmx == last_frame and now - last_print < args.interval:
                continue
            last_frame = dmx
            last_print = now
            if channels:
                values = ", ".join(f"ch{c}={dmx[c - 1]}" for c in channels if c <= len(dmx))
            else:
                values = " ".join(f"{b:3d}" for b in dmx[:16]) + " ..."
            print(f"[{time.strftime('%H:%M:%S')}] from {addr[0]}: {values}")
            if args.decode_fixture:
                result = decoded_pan_tilt(dmx, args)
                if result:
                    pan, tilt = result
                    print(f"           decoded pan={pan:+7.2f}deg  tilt={tilt:+7.2f}deg")
        print("\nStopped.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def cmd_verify(args):
    print("FART closed-loop verification")
    print("==============================")
    print(f"Sending marker {args.marker_id} ({args.pattern}) to {args.group}:{args.port}, "
          f"watching Art-Net universe {args.universe} for the result.")
    print("For best accuracy, set FART's Smoothing to 0 and Lead/lag to 0 while testing.")
    print(f"Tolerance: {args.tolerance:g} degrees. Ctrl+C to stop and see the summary.\n")

    start = time.monotonic()

    def sender():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
        dest = (args.group, args.port)
        interval = 1.0 / args.rate
        try:
            while not _STOP.is_set():
                t = time.monotonic() - start
                x, y, z = marker_position(args.pattern, t, args.x, args.y, args.z, args.radius, args.speed)
                sock.sendto(build_psn_packet([(args.marker_id, x, y, z)]), dest)
                time.sleep(interval)
        finally:
            sock.close()

    sender_thread = threading.Thread(target=sender, daemon=True)
    sender_thread.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", ARTNET_PORT))
    sock.settimeout(0.5)

    samples = 0
    passed = 0
    max_error = 0.0
    try:
        while not _STOP.is_set():
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            parsed = parse_artnet_dmx(data)
            if not parsed:
                continue
            universe, dmx = parsed
            if universe != args.universe:
                continue
            now = time.monotonic() - start
            if now < args.settle:
                continue  # let FART's smoothing catch up before judging it
            decoded = decoded_pan_tilt(dmx, args)
            if decoded is None:
                continue
            actual_pan, actual_tilt = decoded
            marker_xyz = marker_position(args.pattern, now, args.x, args.y, args.z, args.radius, args.speed)
            expected_pan, expected_tilt = expected_pan_tilt(marker_xyz, args)
            pan_error = abs(wrap180(actual_pan - expected_pan))
            tilt_error = abs(actual_tilt - expected_tilt)
            error = max(pan_error, tilt_error)
            max_error = max(max_error, error)
            samples += 1
            ok = error <= args.tolerance
            passed += ok
            status = "OK  " if ok else "FAIL"
            print(f"[{status}] t={now:6.1f}s  expected pan={expected_pan:+7.2f} tilt={expected_tilt:+7.2f}  "
                  f"actual pan={actual_pan:+7.2f} tilt={actual_tilt:+7.2f}  error={error:5.2f}deg")
    except KeyboardInterrupt:
        pass
    finally:
        _STOP.set()
        sock.close()

    print("\n==============================")
    if samples == 0:
        print("No Art-Net frames were received for that universe. Check: FART is running and "
              "started, Output is set to Art-Net, the universe matches, and this machine can "
              "receive its Art-Net traffic (same host or a routed/unicast target).")
        sys.exit(2)
    print(f"{passed}/{samples} samples within {args.tolerance:g} degrees, max error {max_error:.2f} degrees.")
    sys.exit(0 if passed == samples else 1)


# ---------------------------------------------------------------------------
# send-console
# ---------------------------------------------------------------------------

def cmd_send_console(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    frame = bytearray(512)
    if args.mode_channel:
        frame[args.mode_channel - 1] = 255 if args.mode == "auto" else 0
    if args.marker_channel:
        frame[args.marker_channel - 1] = args.marker_id
    packet = build_artnet_dmx(args.universe, frame)
    dest = (args.target, ARTNET_PORT)
    print(f"Sending a simulated console frame to {args.target}:{ARTNET_PORT}, universe {args.universe}: "
          f"mode channel {args.mode_channel or '(none)'} = {args.mode}, "
          f"marker-select channel {args.marker_channel or '(none)'} = {args.marker_id if args.marker_channel else '(none)'}.")
    print("In FART: the fixture's Mode channel / Marker-select channel fields (Lights tab -> "
          "DMX channels -> Live console control) must match the channel numbers given here.")
    if args.repeat:
        print(f"Repeating every {args.interval:g}s. Ctrl+C to stop.")
        try:
            while not _STOP.is_set():
                sock.sendto(packet, dest)
                time.sleep(args.interval)
            print("\nStopped.")
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        sock.sendto(packet, dest)
        print("Sent once. Pass --repeat to keep sending (a single Art-Net frame will eventually "
              "look stale to FART's own console-signal-loss timeout).")
    sock.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    signal.signal(signal.SIGINT, _handle_sigint)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("send-psn", help="Send synthetic OpenFollow-style PSN tracker positions")
    add_psn_network_args(p)
    add_marker_pattern_args(p)
    p.set_defaults(func=cmd_send_psn)

    p = sub.add_parser("monitor-artnet", help="Decode FART's own Art-Net output")
    p.add_argument("--universe", type=int, default=0, help="Art-Net universe to watch (default: 0)")
    p.add_argument("--channels", default="1-8", help="Channels to print, e.g. '1-8,101' (default: 1-8)")
    p.add_argument("--interval", type=float, default=1.0, help="Max seconds between reprinting an unchanged frame (default: 1.0)")
    p.add_argument("--decode-fixture", action="store_true", help="Also decode pan/tilt using --pan-*/--tilt-* channel and range flags")
    add_fixture_args(p)
    p.set_defaults(func=cmd_monitor_artnet)

    p = sub.add_parser("verify", help="Send a marker trajectory and automatically check FART's Art-Net output against it")
    add_psn_network_args(p)
    add_marker_pattern_args(p)
    add_fixture_args(p)
    p.add_argument("--universe", type=int, default=0, help="Art-Net universe FART is outputting to (default: 0)")
    p.add_argument("--tolerance", type=float, default=2.0, help="Allowed pan/tilt error in degrees before flagging FAIL (default: 2.0)")
    p.add_argument("--settle", type=float, default=3.0, help="Seconds to wait before judging results, to let FART's smoothing catch up (default: 3.0)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("send-console", help="Simulate a lighting console driving FART's live console-control channels")
    p.add_argument("--target", default="127.0.0.1", help="Where to send the Art-Net frame (default: 127.0.0.1)")
    p.add_argument("--universe", type=int, default=0, help="Universe the fixture and its control fixture are patched on (default: 0)")
    p.add_argument("--mode-channel", type=int, default=0, help="Absolute channel for the Mode control, matching FART's fixture setting (0 = don't set)")
    p.add_argument("--marker-channel", type=int, default=0, help="Absolute channel for Marker-select, matching FART's fixture setting (0 = don't set)")
    p.add_argument("--mode", choices=["manual", "auto"], default="auto", help="Mode to send (default: auto)")
    p.add_argument("--marker-id", type=int, default=0, help="Marker ID to send on the marker-select channel (default: 0 = use FART's configured default marker)")
    p.add_argument("--repeat", action="store_true", help="Keep sending instead of a single packet")
    p.add_argument("--interval", type=float, default=1.0, help="Seconds between repeats (default: 1.0)")
    p.set_defaults(func=cmd_send_console)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
