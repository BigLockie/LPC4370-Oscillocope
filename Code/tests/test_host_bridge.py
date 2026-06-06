"""Block 2 verification harness.

One file with six sub-tests, selected by CLI flag, mirroring the test
plan in the Block 2 design document.

  --log-packets       Test 1 + Test 2: prove framing and ≤7-byte packet size
  --verify-checksums  Test 3: independently recompute the XOR checksum
  --test-data         Test 4: feed a 0..1023 ramp, verify voltage conversion
  --test-corrupt      Test 5: feed a packet with a bad checksum, verify reject
  --debug-threads     Test 6: log array length + thread name on every callback

  --port <COM>        Use a real serial port (defaults to MockMCU otherwise)
  --all               Run every sub-test sequentially and report a summary

Each sub-test prints `[RESULT] PASS` or `[RESULT] FAIL` so the demo can
screenshot the terminal as evidence.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host_bridge import (  # noqa: E402
    HostBridge, serialize_command, xor_checksum,
    START_BYTE, DATA_TYPE_CH1, SAMPLES_PER_BLOCK,
    ADC_MAX, V_REF, V_REF_PER_DIV,
    CMD_SET_TIMEBASE, CMD_SET_VDIV, CMD_SET_VOFFSET,
    CMD_SET_TRIGGER_LEVEL, CMD_SET_TRIGGER_MODE, CMD_SET_TRIGGER_SOURCE,
    CMD_SET_CHANNEL, CMD_RUN, CMD_STOP, CMD_SINGLE,
)
from mock_mcu import MockMCU  # noqa: E402


# Payload length per command ID - used to slice multi-packet blobs (e.g.
# set_trigger now emits level + mode + source as three back-to-back packets).
_PAYLOAD_LEN_FOR_ID = {
    CMD_SET_TIMEBASE: 4, CMD_SET_VDIV: 4, CMD_SET_VOFFSET: 4,
    CMD_SET_TRIGGER_LEVEL: 4, CMD_SET_TRIGGER_MODE: 1,
    CMD_SET_TRIGGER_SOURCE: 1, CMD_SET_CHANNEL: 2,
    CMD_RUN: 0, CMD_STOP: 0, CMD_SINGLE: 0,
}


def _split_into_packets(blob: bytes):
    """Split a serialize_command() output into individual on-wire packets."""
    out = []
    i = 0
    while i < len(blob):
        if blob[i] != START_BYTE:
            i += 1
            continue
        cid  = blob[i + 1]
        plen = _PAYLOAD_LEN_FOR_ID.get(cid, 0)
        end  = i + 2 + plen + 1   # start + cid + payload + checksum
        out.append(blob[i:end])
        i = end
    return out


# ---------------------------------------------------------------------------
# Sub-tests
# ---------------------------------------------------------------------------

ALL_COMMANDS = [
    {"cmd": "set_timebase", "value": 1e-3},
    {"cmd": "set_vdiv",     "value": 0.5},
    {"cmd": "set_voffset",  "value": -1.25},
    {"cmd": "set_trigger",  "level": 1.5,
                            "mode": "rising", "source": "Ch1"},
    {"cmd": "set_trigger_mode",   "mode": "falling"},
    {"cmd": "set_trigger_source", "source": "Ch2"},
    {"cmd": "set_channel",  "channel": 1, "enabled": True},
    {"cmd": "run"},
    {"cmd": "stop"},
    {"cmd": "single"},
]


def test_packet_format_and_size() -> bool:
    """Tests 1 & 2: every command produces correctly framed packet(s) ≤7 bytes each.

    `set_trigger` now emits 3 back-to-back packets (level, mode, source),
    so we walk each sub-packet and validate it independently.
    """
    print("\n=== Test 1+2: packet format + max size ===")
    ok = True
    for cmd in ALL_COMMANDS:
        blob = serialize_command(cmd)
        packets = _split_into_packets(blob)
        if not packets:
            ok = False
            print(f"  [FAIL] {cmd['cmd']:<22} produced no packets")
            continue
        for pkt in packets:
            size_ok  = 0 < len(pkt) <= 7
            start_ok = pkt[0] == START_BYTE
            cksum_ok = xor_checksum(pkt[:-1]) == pkt[-1]
            line_ok  = size_ok and start_ok and cksum_ok
            ok = ok and line_ok
            hex_pkt = " ".join(f"{b:02X}" for b in pkt)
            print(f"  [{ 'OK' if line_ok else 'FAIL' }] "
                  f"len={len(pkt)} start=0x{pkt[0]:02X} "
                  f"cksum=0x{pkt[-1]:02X}  {cmd['cmd']:<22} -> {hex_pkt}")
    print(f"[RESULT] {'PASS' if ok else 'FAIL'}")
    return ok


def test_verify_checksums() -> bool:
    """Test 3: independently recompute XOR for every (sub-)packet."""
    print("\n=== Test 3: XOR checksum verification ===")
    ok = True
    for cmd in ALL_COMMANDS:
        blob = serialize_command(cmd)
        for idx, pkt in enumerate(_split_into_packets(blob)):
            calc  = xor_checksum(pkt[:-1])
            tx    = pkt[-1]
            match = calc == tx
            ok    = ok and match
            label = f"{cmd['cmd']}[{idx}]" if len(_split_into_packets(blob)) > 1 else cmd['cmd']
            print(f"  [{ 'OK' if match else 'FAIL' }] "
                  f"{label:<22}  calc=0x{calc:02X} tx=0x{tx:02X}")
    print(f"[RESULT] {'PASS' if ok else 'FAIL'}")
    return ok


def test_data_conversion() -> bool:
    """Test 4: feed a 0..1023 ramp through the Backend, verify voltage scaling."""
    print("\n=== Test 4: ADC ramp -> voltage conversion ===")
    received: list[dict] = []
    mcu = MockMCU(mode="idle")
    backend = HostBridge(mcu)
    backend.register_display_callback(received.append)
    backend.start()

    # Backend's local vdiv defaults to 1.0; set it explicitly for clarity.
    backend.dispatch({"cmd": "set_vdiv", "value": 1.0})
    time.sleep(0.05)

    pattern = list(range(SAMPLES_PER_BLOCK))   # 0, 1, 2, ..., 1023
    mcu.send_block(pattern, channel=1)

    # Spin pump on the main thread (== this thread) until we get the block
    deadline = time.time() + 2.0
    while time.time() < deadline and not received:
        backend.pump()
        time.sleep(0.01)
    backend.stop()

    if not received:
        print("  [FAIL] no data block received from MockMCU")
        print("[RESULT] FAIL")
        return False

    rec = received[0]
    counts = rec["samples_raw"]
    volts  = rec["voltage"]

    expected = (np.arange(SAMPLES_PER_BLOCK) / ADC_MAX) * V_REF \
               * (1.0 / V_REF_PER_DIV)

    len_ok    = len(counts) == SAMPLES_PER_BLOCK and len(volts) == SAMPLES_PER_BLOCK
    counts_ok = bool(np.array_equal(counts, np.arange(SAMPLES_PER_BLOCK)))
    volts_ok  = bool(np.allclose(volts, expected, atol=1e-9))

    print(f"  array length      : {len(counts)}        ({'OK' if len_ok else 'FAIL'})")
    print(f"  raw counts match  : {counts_ok}             ({'OK' if counts_ok else 'FAIL'})")
    print(f"  voltage formula OK: {volts_ok}             ({'OK' if volts_ok else 'FAIL'})")
    print(f"  sample[0]   raw={counts[0]:4d}  V={volts[0]:+.4f}  (expected {expected[0]:+.4f})")
    print(f"  sample[512] raw={counts[512]:4d}  V={volts[512]:+.4f}  (expected {expected[512]:+.4f})")
    print(f"  sample[-1]  raw={counts[-1]:4d}  V={volts[-1]:+.4f}  (expected {expected[-1]:+.4f})")

    ok = len_ok and counts_ok and volts_ok
    print(f"[RESULT] {'PASS' if ok else 'FAIL'}")
    return ok


def test_corrupt_rejection() -> bool:
    """Test 5: corrupted packets must NOT reach the Display callback."""
    print("\n=== Test 5: corrupted packet rejection ===")
    received: list[dict] = []
    mcu = MockMCU(mode="idle")
    backend = HostBridge(mcu)
    backend.register_display_callback(received.append)
    backend.start()

    bad_pattern = [42] * SAMPLES_PER_BLOCK   # known sentinel value
    mcu.send_block(bad_pattern, channel=1, corrupt=True)

    deadline = time.time() + 1.5
    while time.time() < deadline:
        backend.pump()
        time.sleep(0.01)

    err_count = len(backend.error_log)
    backend.stop()

    no_data_through = len(received) == 0
    error_logged    = any("checksum" in e for e in backend.error_log) or err_count > 0

    print(f"  display callbacks fired  : {len(received)}   ({'OK' if no_data_through else 'FAIL'})")
    print(f"  error log entries        : {err_count}")
    if backend.error_log:
        for e in backend.error_log:
            print(f"    - {e}")
    ok = no_data_through and error_logged
    print(f"[RESULT] {'PASS' if ok else 'FAIL'}")
    return ok


def test_debug_threads() -> bool:
    """Test 6: array lengths == 1024 and callback runs on MainThread."""
    print("\n=== Test 6: array length + thread safety ===")
    main_thread_name = threading.current_thread().name
    log: list[tuple[int, int, str]] = []

    def cb(data: dict) -> None:
        log.append((len(data["time"]), len(data["voltage"]),
                    threading.current_thread().name))

    mcu = MockMCU(mode="dual")  # auto-emits at MockMCU's default cadence
    backend = HostBridge(mcu, debug_threads=True)
    backend.register_display_callback(cb)
    backend.start()

    deadline = time.time() + 2.5
    while time.time() < deadline and len(log) < 5:
        backend.pump()
        time.sleep(0.05)
    backend.stop()

    if not log:
        print("  [FAIL] no callbacks fired")
        print("[RESULT] FAIL")
        return False

    lengths_ok = all(t == SAMPLES_PER_BLOCK and v == SAMPLES_PER_BLOCK
                     for t, v, _ in log)
    threads_ok = all(name == main_thread_name for _, _, name in log)

    print(f"  callbacks observed       : {len(log)}")
    print(f"  all len(time)==1024      : {lengths_ok}")
    print(f"  all len(voltage)==1024   : {lengths_ok}")
    print(f"  all on main thread       : {threads_ok}  (expected '{main_thread_name}')")
    print(f"  sample callbacks:")
    for t, v, name in log[:3]:
        print(f"    len(time)={t} len(voltage)={v} thread='{name}'")
    ok = lengths_ok and threads_ok
    print(f"[RESULT] {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------------------
# Live demo (--port mode): just stream MCU data and print summaries.
# ---------------------------------------------------------------------------

def live_demo(port: str) -> None:
    print(f"\n=== LIVE: connecting to {port} at 115200 8N1 ===")
    try:
        import serial  # type: ignore
    except ImportError:
        print("pyserial not installed. Run: pip install pyserial")
        sys.exit(1)
    transport = serial.Serial(port, 115200, timeout=0.1)
    backend = Backend(transport, log_packets=True, verify_checksums=True,
                      debug_threads=True)

    def show(data: dict) -> None:
        v = data["voltage"]
        print(f"  Ch{data['channel']}  N={len(v)}  "
              f"min={v.min():+.3f}V  max={v.max():+.3f}V  "
              f"thread={data.get('_thread', '?')}")

    backend.register_display_callback(show)
    backend.start()
    backend.dispatch({"cmd": "set_vdiv", "value": 1.0})
    backend.dispatch({"cmd": "set_timebase", "value": 1e-3})
    backend.dispatch({"cmd": "run"})
    try:
        while True:
            backend.pump()
            time.sleep(0.05)
    except KeyboardInterrupt:
        backend.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Block 2 (Backend) verification harness")
    p.add_argument("--port", help="Real serial port (else uses MockMCU)")
    p.add_argument("--log-packets", action="store_true",
                   help="Test 1+2: dump every TX packet length + framing")
    p.add_argument("--verify-checksums", action="store_true",
                   help="Test 3: recompute XOR for every TX packet")
    p.add_argument("--test-data", action="store_true",
                   help="Test 4: ramp pattern -> voltage conversion check")
    p.add_argument("--test-corrupt", action="store_true",
                   help="Test 5: corrupted packet must be rejected")
    p.add_argument("--debug-threads", action="store_true",
                   help="Test 6: array length + main-thread callback check")
    p.add_argument("--all", action="store_true",
                   help="Run all sub-tests sequentially with summary")
    args = p.parse_args()

    if args.port and not (args.log_packets or args.verify_checksums or
                          args.test_data or args.test_corrupt or
                          args.debug_threads or args.all):
        live_demo(args.port)
        return 0

    requested = []
    if args.all or args.log_packets:
        requested.append(("Test 1+2 framing/size", test_packet_format_and_size))
    if args.all or args.verify_checksums:
        requested.append(("Test 3 checksums",       test_verify_checksums))
    if args.all or args.test_data:
        requested.append(("Test 4 ADC conversion",  test_data_conversion))
    if args.all or args.test_corrupt:
        requested.append(("Test 5 corrupt reject",  test_corrupt_rejection))
    if args.all or args.debug_threads:
        requested.append(("Test 6 thread safety",   test_debug_threads))

    if not requested:
        p.print_help()
        return 1

    results = []
    for name, fn in requested:
        results.append((name, fn()))

    print("\n=== Summary ===")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    all_ok = all(ok for _, ok in results)
    print(f"\nOverall: [RESULT] {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
