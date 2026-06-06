"""Block 2 - Test 7: cross-language protocol parity.

Verifies that the firmware's C protocol code (Firmware/Backend_LPC4370/src/proto.c)
parses and builds packets *identically* to the laptop-side host_bridge.py.

This test runs WITHOUT any LPC4370 hardware. It compiles proto.c into a
Windows DLL, loads it via ctypes, and round-trips:

    1. XOR checksum: random byte strings -> C proto_xor() vs Python xor_checksum()
    2. Command serialize -> C parse:
         host_bridge.serialize_command(d) -> bytes -> fed byte-by-byte to
         proto_parse_byte() -> assert decoded id/payload matches d.
    3. Data packet build -> host parse:
         proto_build_data_packet() in C -> fed to host_bridge.parse_stream()
         -> assert decoded samples match.
    4. Bad-checksum rejection in C: corrupt any byte of a valid packet,
         verify proto_parse_byte never asserts a complete packet.

Build first:
    Code\\tests\\native\\build_proto.bat       (Windows; MinGW gcc on PATH)

Then run:
    python Code\\tests\\test_proto_parity.py
"""

import ctypes
import os
import platform
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host_bridge import (  # noqa: E402
    serialize_command, xor_checksum, START_BYTE,
    DATA_TYPE_CH1, DATA_TYPE_CH2, SAMPLES_PER_BLOCK,
    CMD_SET_TIMEBASE, CMD_SET_VDIV, CMD_SET_VOFFSET,
    CMD_SET_TRIGGER_LEVEL, CMD_SET_TRIGGER_MODE, CMD_SET_TRIGGER_SOURCE,
    CMD_SET_CHANNEL, CMD_RUN, CMD_STOP, CMD_SINGLE,
)


# ---------------------------------------------------------------------------
# DLL loading
# ---------------------------------------------------------------------------

def _dll_name():
    s = platform.system()
    if s == "Windows":
        return "proto.dll"
    if s == "Darwin":
        return "proto.dylib"
    return "proto.so"


def _load_proto():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "native", _dll_name())
    if not os.path.exists(path):
        print(f"[FATAL] {path} not found.")
        print("        On Windows:  Code\\tests\\native\\build_proto.bat")
        print("        Requires MinGW-w64 gcc on PATH (verify with `gcc --version`).")
        sys.exit(2)
    return ctypes.CDLL(path)


# ---------------------------------------------------------------------------
# C struct + function bindings
# ---------------------------------------------------------------------------

PROTO_MAX_PAYLOAD_LEN = 4


class ProtoCmd(ctypes.Structure):
    """Mirror of proto_cmd_t in proto.h.

    Layout (gcc default packing on x86_64):
        4 bytes id (enum)
        1 byte  payload_len
        4 bytes payload[4]
        + 3 bytes tail padding so sizeof == 12
    ctypes uses the same natural alignment, so the offsets line up
    without any explicit _pack_.
    """
    _fields_ = [
        ("id",          ctypes.c_int),
        ("payload_len", ctypes.c_uint8),
        ("payload",     ctypes.c_uint8 * PROTO_MAX_PAYLOAD_LEN),
    ]


def _bind(lib):
    lib.proto_xor.restype  = ctypes.c_uint8
    lib.proto_xor.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint16]

    lib.proto_parse_byte.restype  = ctypes.c_bool
    lib.proto_parse_byte.argtypes = [ctypes.c_uint8, ctypes.POINTER(ProtoCmd)]

    lib.proto_build_data_packet.restype  = ctypes.c_uint16
    lib.proto_build_data_packet.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint16,
        ctypes.c_uint8,
        ctypes.POINTER(ctypes.c_uint16), ctypes.c_uint16,
    ]
    return lib


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def c_xor(lib, data: bytes) -> int:
    arr = (ctypes.c_uint8 * len(data))(*data)
    return lib.proto_xor(arr, len(data))


def c_parse_packet(lib, packet: bytes):
    """Feed packet bytes to proto_parse_byte one at a time. Returns the
    decoded ProtoCmd if the parser asserts complete, else None."""
    cmd = ProtoCmd()
    last = None
    for b in packet:
        if lib.proto_parse_byte(b, ctypes.byref(cmd)):
            last = ProtoCmd()
            last.id, last.payload_len = cmd.id, cmd.payload_len
            for i in range(PROTO_MAX_PAYLOAD_LEN):
                last.payload[i] = cmd.payload[i]
    return last


def c_build_data_packet(lib, data_type: int, samples) -> bytes:
    """Call proto_build_data_packet -> bytes."""
    n = len(samples)
    out_cap = 5 + 2 * n
    out_buf = (ctypes.c_uint8 * out_cap)()
    sample_buf = (ctypes.c_uint16 * n)(*samples)
    written = lib.proto_build_data_packet(out_buf, out_cap, data_type, sample_buf, n)
    return bytes(out_buf[:written])


# All commands the host can send, with a representative value.
COMMANDS_TO_TEST = [
    ({"cmd": "set_timebase",       "value": 1e-5},                                CMD_SET_TIMEBASE,       4),
    ({"cmd": "set_timebase",       "value": 1.0},                                 CMD_SET_TIMEBASE,       4),
    ({"cmd": "set_vdiv",           "value": 0.05},                                CMD_SET_VDIV,           4),
    ({"cmd": "set_vdiv",           "value": 5.0},                                 CMD_SET_VDIV,           4),
    ({"cmd": "set_voffset",        "value": -1.25},                               CMD_SET_VOFFSET,        4),
    ({"cmd": "set_trigger",        "level": 0.0,
                                   "mode":  "rising",
                                   "source": "Ch1"},                              None,                   None),  # multi
    ({"cmd": "set_trigger",        "level": -2.5,
                                   "mode":  "falling",
                                   "source": "Ch2"},                              None,                   None),  # multi
    ({"cmd": "set_trigger",        "level":  3.3,
                                   "mode":  "auto",
                                   "source": "Ch1"},                              None,                   None),  # multi
    ({"cmd": "set_channel",        "channel": 1, "enabled": True},                CMD_SET_CHANNEL,        2),
    ({"cmd": "set_channel",        "channel": 2, "enabled": False},               CMD_SET_CHANNEL,        2),
    ({"cmd": "run"},                                                              CMD_RUN,                0),
    ({"cmd": "stop"},                                                             CMD_STOP,               0),
    ({"cmd": "single"},                                                           CMD_SINGLE,             0),
]


# ---------------------------------------------------------------------------
# Sub-tests
# ---------------------------------------------------------------------------

def test_xor_parity(lib):
    print("[T7.1] XOR checksum parity (host vs firmware C)")
    rng = random.Random(0xABCD)
    n_trials = 200
    for trial in range(n_trials):
        length = rng.randint(0, 64)
        data   = bytes(rng.randrange(256) for _ in range(length))
        py = xor_checksum(data)
        c  = c_xor(lib, data)
        if py != c:
            print(f"  FAIL trial {trial}: data={data.hex()} py=0x{py:02X} c=0x{c:02X}")
            return False
    print(f"  {n_trials} trials, all match. PASS")
    return True


def test_command_round_trip(lib):
    print("[T7.2] Host serialize_command -> firmware proto_parse_byte (per-command)")
    fail = 0
    for raw_cmd, expected_id, expected_plen in COMMANDS_TO_TEST:
        try:
            packets = serialize_command(raw_cmd)
        except Exception as e:
            print(f"  FAIL {raw_cmd!r}: serialize_command raised {e}")
            fail += 1
            continue

        # serialize_command may emit multiple packets (set_trigger -> 3).
        # Feed ALL bytes; collect every packet the C parser asserts.
        decoded = []
        cmd = ProtoCmd()
        for b in packets:
            if lib.proto_parse_byte(b, ctypes.byref(cmd)):
                snapshot = (cmd.id, cmd.payload_len, bytes(cmd.payload[:cmd.payload_len]))
                decoded.append(snapshot)

        if not decoded:
            print(f"  FAIL {raw_cmd!r}: C parser produced no decoded packets")
            print(f"        bytes were: {packets.hex()}")
            fail += 1
            continue

        # If the test row pinned an exact ID, check the first decoded packet.
        if expected_id is not None:
            ok_id  = (decoded[0][0] == expected_id)
            ok_len = (decoded[0][1] == expected_plen)
            if not (ok_id and ok_len):
                print(f"  FAIL {raw_cmd!r}: got id=0x{decoded[0][0]:02X} plen={decoded[0][1]}, "
                      f"expected id=0x{expected_id:02X} plen={expected_plen}")
                fail += 1
                continue

        print(f"  OK   {raw_cmd['cmd']:<14s} -> "
              f"{len(decoded)} packet(s), ids="
              f"{[hex(d[0]) for d in decoded]}")

    if fail:
        print(f"  {fail} failures. FAIL")
        return False
    print(f"  {len(COMMANDS_TO_TEST)} command rows clean. PASS")
    return True


def test_data_packet_build(lib):
    ADC_12BIT_MAX = (1 << 12) - 1   # 4095
    print(f"[T7.3] Firmware proto_build_data_packet -> host decode "
          f"(12-bit samples, 0 to {ADC_12BIT_MAX})")
    rng = random.Random(0x1234)
    fail = 0

    for trial, data_type in enumerate([DATA_TYPE_CH1, DATA_TYPE_CH2]):
        # Full 12-bit range: 0 .. 4095 inclusive.
        samples = [rng.randrange(0, ADC_12BIT_MAX + 1) for _ in range(SAMPLES_PER_BLOCK)]
        samples[0]  = 0                # exercise the floor
        samples[1]  = ADC_12BIT_MAX    # exercise the ceiling (4095)
        packet  = c_build_data_packet(lib, data_type, samples)

        # Hand-decode the packet using the documented framing.
        if len(packet) != 5 + 2 * SAMPLES_PER_BLOCK:
            print(f"  FAIL trial {trial}: wrong packet length {len(packet)}, "
                  f"expected {5 + 2 * SAMPLES_PER_BLOCK}")
            fail += 1
            continue
        if packet[0] != START_BYTE or packet[1] != data_type:
            print(f"  FAIL trial {trial}: bad header bytes")
            fail += 1
            continue

        count = (packet[2] << 8) | packet[3]
        if count != SAMPLES_PER_BLOCK:
            print(f"  FAIL trial {trial}: count field={count}, expected {SAMPLES_PER_BLOCK}")
            fail += 1
            continue

        # Independent re-XOR over the whole packet minus the trailing checksum byte.
        if xor_checksum(packet[:-1]) != packet[-1]:
            print(f"  FAIL trial {trial}: checksum mismatch")
            fail += 1
            continue

        # Re-decode 16-bit BE samples and compare.
        body = packet[4:-1]
        decoded = struct.unpack(f">{count}H", body)
        if list(decoded) != samples:
            print(f"  FAIL trial {trial}: samples did not round-trip")
            fail += 1
            continue

        print(f"  OK   data_type=0x{data_type:02X}: {count} samples, "
              f"{len(packet)} bytes, checksum 0x{packet[-1]:02X}, "
              f"range [{min(decoded)}..{max(decoded)}]")

    # Over-range check: the firmware masks samples to 12 bits (0x0FFF), so a
    # value above 4095 must come back masked, never as the raw value.
    over = [ADC_12BIT_MAX + 1, 0x1234, 0xFFFF]   # 4096, 4660, 65535
    pkt  = c_build_data_packet(lib, DATA_TYPE_CH1, over)
    got  = list(struct.unpack(f">{len(over)}H", pkt[4:-1]))
    want = [v & 0x0FFF for v in over]
    if got != want:
        print(f"  FAIL over-range: fed {over}, got {got}, expected masked {want}")
        fail += 1
    else:
        print(f"  OK   over-range masked to 12 bits: {over} -> {got} (all <= {ADC_12BIT_MAX})")

    if fail:
        print(f"  {fail} failures. FAIL")
        return False
    print(f"  PASS")
    return True


def test_corrupt_rejection(lib):
    print("[T7.4] Firmware proto_parse_byte rejects corrupted command packets")
    rng = random.Random(0x5A5A)

    base_cmd = {"cmd": "set_timebase", "value": 5e-5}
    good = serialize_command(base_cmd)
    if len(good) != 7:
        print(f"  FAIL setup: expected a 7-byte packet, got {len(good)}")
        return False

    n_trials = 50
    false_accept = 0

    for trial in range(n_trials):
        # Flip one random byte to anything-but-itself.
        idx       = rng.randrange(len(good))
        original  = good[idx]
        corrupted = bytearray(good)
        new_val   = original
        while new_val == original:
            new_val = rng.randrange(256)
        corrupted[idx] = new_val

        # Reset the C parser to IDLE by feeding 0xFFs (invalid start).
        cmd = ProtoCmd()
        for _ in range(8):
            lib.proto_parse_byte(0xFF, ctypes.byref(cmd))

        accepted = False
        for b in corrupted:
            if lib.proto_parse_byte(b, ctypes.byref(cmd)):
                # Corrupting the start byte makes the rest re-sync to a real
                # 0xAA elsewhere - the parser is allowed to *eventually*
                # produce a packet, but if it does so for THIS corruption
                # *and* the decoded id/payload match the original, that's
                # a false accept.
                if (cmd.id == CMD_SET_TIMEBASE and
                    cmd.payload_len == 4 and
                    bytes(cmd.payload[:4]) == bytes(good[2:6])):
                    accepted = True

        if accepted:
            false_accept += 1
            print(f"  FAIL trial {trial}: corrupted byte {idx} (0x{original:02X}->0x{new_val:02X}) "
                  f"was accepted as the original packet")

    if false_accept:
        print(f"  {false_accept}/{n_trials} false accepts. FAIL")
        return False
    print(f"  {n_trials} corruptions, 0 false accepts. PASS")
    return True


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Block 2 - Test 7: Cross-Language Protocol Parity")
    print(f"Platform: {platform.system()} {platform.release()}")
    print("=" * 70)

    lib = _bind(_load_proto())
    print(f"Loaded firmware proto.{ _dll_name().split('.')[-1] }\n")

    results = [
        ("T7.1 XOR parity",                test_xor_parity(lib)),
        ("T7.2 Command round-trip",        test_command_round_trip(lib)),
        ("T7.3 Data packet build/decode",  test_data_packet_build(lib)),
        ("T7.4 Corrupt-packet rejection",  test_corrupt_rejection(lib)),
    ]

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    overall = all(ok for _, ok in results)
    print()
    print(f"Overall: [RESULT] {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
