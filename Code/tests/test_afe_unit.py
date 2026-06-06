"""Block 2 - Test 8: AFE control logic (firmware unit test, no hardware).

Verifies the three claimed properties of the `backend_afe_ctrl` interface
by compiling the firmware's afe.c against a stub LPCOpen layer that
records every Chip_SCU_PinMuxSet / Chip_GPIO_SetPinDIROutput /
Chip_GPIO_SetPinState call. The Python side then inspects the recorded
call sequence to assert:

    T8.1  Control-line allocation     - every SEL + SELSSR pin from the
                                         design's pin_map.h is actually
                                         set up as a GPIO output by
                                         afe_init().

    T8.2  One-hot gain encoding       - calling afe_set_gain() leaves
                                         exactly one of the four SEL
                                         lines high for the selected gain
                                         and the other three low.

    T8.3  Break-before-make switching - afe_set_gain() drives ALL four
                                         SELs low BEFORE asserting the
                                         target SEL high (so there is
                                         never a moment with two SELs
                                         simultaneously asserted).

Build first:
    Code\\tests\\native\\afe_test\\build_afe.bat        (Windows; MinGW gcc)
    bash Code/tests/native/afe_test/build_afe.sh        (macOS / Linux)

Then run:
    python Code\\tests\\test_afe_unit.py
"""

import ctypes
import os
import platform
import sys


# ---------------------------------------------------------------------------
# Pin map (must mirror Firmware/Backend_LPC4370/inc/pin_map.h)
# ---------------------------------------------------------------------------

CH1_SEL = [(3, 1), (0, 12), (0, 3), (0, 13)]   # SEL1..SEL4 -> (port, bit)
CH2_SEL = [(0, 4), (0, 0),  (0, 1), (1, 5)]

CH1_SELSSR = (0, 15)
CH2_SELSSR = (0, 2)

GAIN_0_256 = 0
GAIN_0_833 = 1
GAIN_2_564 = 2
GAIN_10_0  = 3


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

def _lib_name():
    s = platform.system()
    if s == "Windows": return "afe.dll"
    if s == "Darwin":  return "afe.dylib"
    return "afe.so"


def _load_afe():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "native", "afe_test", _lib_name())
    if not os.path.exists(path):
        print(f"[FATAL] {path} not found.")
        print("        Build first:")
        print("          Windows:  Code\\tests\\native\\afe_test\\build_afe.bat")
        print("          Unix:     bash Code/tests/native/afe_test/build_afe.sh")
        sys.exit(2)
    return ctypes.CDLL(path)


# ---------------------------------------------------------------------------
# C bindings
# ---------------------------------------------------------------------------

OP_SCU_MUX     = 0
OP_GPIO_DIR    = 1
OP_GPIO_SET    = 2


class CallRecord(ctypes.Structure):
    """Mirror of call_record_t in lpcopen_stub.c."""
    _fields_ = [
        ("op",    ctypes.c_int),
        ("port",  ctypes.c_int),
        ("pin",   ctypes.c_int),
        ("value", ctypes.c_int),
    ]


def _bind(lib):
    lib.afe_init.restype                 = None
    lib.afe_init.argtypes                = []

    lib.afe_set_gain.restype             = None
    lib.afe_set_gain.argtypes            = [ctypes.c_uint8, ctypes.c_int]   # channel, gain enum

    lib.afe_set_coupling_dc.restype      = None
    lib.afe_set_coupling_dc.argtypes     = [ctypes.c_uint8, ctypes.c_bool]

    lib.afe_test_log_clear.restype       = None
    lib.afe_test_log_clear.argtypes      = []

    lib.afe_test_log     = (CallRecord * 1024).in_dll(lib, "afe_test_log")
    lib.afe_test_log_len = ctypes.c_int.in_dll(lib, "afe_test_log_len")
    return lib


def snapshot_log(lib):
    n = lib.afe_test_log_len.value
    return [(lib.afe_test_log[i].op,
             lib.afe_test_log[i].port,
             lib.afe_test_log[i].pin,
             lib.afe_test_log[i].value) for i in range(n)]


def gpio_sets(log):
    """Filter the log to just (port, bit) -> value GPIO state writes."""
    return [(p, b, v) for (op, p, b, v) in log if op == OP_GPIO_SET]


# ---------------------------------------------------------------------------
# Sub-tests
# ---------------------------------------------------------------------------

def test_init_allocates_all_pins(lib):
    print("[T8.1] afe_init() configures every SEL + SELSSR pin as a GPIO output")
    lib.afe_test_log_clear()
    lib.afe_init()
    log = snapshot_log(lib)

    expected_pins = (CH1_SEL + CH2_SEL + [CH1_SELSSR, CH2_SELSSR])
    declared_outputs = {(p, b) for (op, p, b, _) in log if op == OP_GPIO_DIR}
    declared_muxed   = {(p, b) for (op, p, b, _) in log if op == OP_SCU_MUX} \
                        if False else None  # SCU port/pin space ≠ GPIO port/bit space

    # Every gain + coupling pin should appear as a DIR-out write
    missing = [p for p in expected_pins if p not in declared_outputs]
    if missing:
        print(f"  FAIL - pin(s) never declared as outputs: {missing}")
        return False

    # And every gain + coupling pin should have at least one Chip_SCU_PinMuxSet
    n_scu = sum(1 for (op, *_rest) in log if op == OP_SCU_MUX)
    if n_scu < len(expected_pins):
        print(f"  FAIL - expected ≥{len(expected_pins)} SCU pin-mux writes, got {n_scu}")
        return False

    print(f"  {len(expected_pins)} pins all configured as outputs, {n_scu} SCU mux writes. PASS")
    return True


def test_one_hot_gain(lib):
    print("[T8.2] afe_set_gain() leaves exactly one SEL high per gain selection")
    fail = 0
    for channel, table in ((1, CH1_SEL), (2, CH2_SEL)):
        for gain_idx in range(4):
            lib.afe_test_log_clear()
            lib.afe_set_gain(channel, gain_idx)
            sets = gpio_sets(snapshot_log(lib))

            # Compute final state of each SEL pin = last GPIO set on that pin
            final = {}
            for (p, b, v) in sets:
                if (p, b) in table:
                    final[(p, b)] = v

            high = [k for k, v in final.items() if v == 1]
            low  = [k for k, v in final.items() if v == 0]
            target = table[gain_idx]
            ok = (high == [target] and set(low) == set(table) - {target})

            mark = " OK " if ok else "FAIL"
            print(f"  [{mark}]  Ch{channel} gain idx={gain_idx} -> high={high} expected=[{target}]")
            if not ok:
                fail += 1

    if fail:
        print(f"  {fail} cases failed. FAIL")
        return False
    print("  8 (channel × gain) combinations, all one-hot. PASS")
    return True


def test_break_before_make(lib):
    print("[T8.3] afe_set_gain() clears all SELs BEFORE asserting the target")
    fail = 0
    for channel, table in ((1, CH1_SEL), (2, CH2_SEL)):
        # Pre-set a different gain so there's an actual transition to observe
        lib.afe_set_gain(channel, GAIN_0_256)
        for gain_idx in (GAIN_2_564, GAIN_10_0, GAIN_0_833):
            lib.afe_test_log_clear()
            lib.afe_set_gain(channel, gain_idx)
            sets = gpio_sets(snapshot_log(lib))
            # Look at the order of writes on the four SEL pins for this channel
            sel_writes = [(p, b, v) for (p, b, v) in sets if (p, b) in table]

            target_pin = table[gain_idx]
            # Find the index in the sequence where target_pin is asserted high
            try:
                first_high = next(i for i, (p, b, v) in enumerate(sel_writes)
                                  if (p, b) == target_pin and v == 1)
            except StopIteration:
                print(f"  FAIL  Ch{channel} idx={gain_idx} - target SEL never set high")
                fail += 1
                continue

            # Every write BEFORE first_high must be a clear (value == 0)
            non_clears_before = [(p, b, v) for (p, b, v) in sel_writes[:first_high] if v != 0]
            # All four SELs must have been cleared somewhere before first_high
            cleared_pins = {(p, b) for (p, b, v) in sel_writes[:first_high] if v == 0}

            ok = (not non_clears_before) and cleared_pins == set(table)
            mark = " OK " if ok else "FAIL"
            print(f"  [{mark}]  Ch{channel} idx={gain_idx} -> "
                  f"{len(cleared_pins)} clears before any set; first_high at step {first_high}")
            if not ok:
                fail += 1

    if fail:
        print(f"  {fail} cases failed. FAIL")
        return False
    print("  6 transitions, all break-before-make. PASS")
    return True


def test_coupling_control(lib):
    """Bonus check - toggling the coupling control flips the right pin."""
    print("[T8.4] afe_set_coupling_dc() drives the SELSSR pin (bonus)")
    fail = 0
    for ch, pin in ((1, CH1_SELSSR), (2, CH2_SELSSR)):
        for dc in (False, True):
            lib.afe_test_log_clear()
            lib.afe_set_coupling_dc(ch, dc)
            sets = gpio_sets(snapshot_log(lib))
            ok = [(p, b, v) for (p, b, v) in sets if (p, b) == pin] == [(pin[0], pin[1], 1 if dc else 0)]
            mark = " OK " if ok else "FAIL"
            print(f"  [{mark}]  Ch{ch} coupling={'DC' if dc else 'AC'} -> pin {pin} = {1 if dc else 0}")
            if not ok:
                fail += 1
    if fail:
        print(f"  {fail} cases failed. FAIL"); return False
    print("  PASS")
    return True


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Block 2 - Test 8: AFE Control Logic (firmware unit test)")
    print(f"Platform: {platform.system()} {platform.release()}")
    print("=" * 70)
    lib = _bind(_load_afe())
    print(f"Loaded firmware afe.{_lib_name().split('.')[-1]}\n")

    results = [
        ("T8.1 Pin allocation",       test_init_allocates_all_pins(lib)),
        ("T8.2 One-hot gain encoding", test_one_hot_gain(lib)),
        ("T8.3 Break-before-make",     test_break_before_make(lib)),
        ("T8.4 Coupling control",      test_coupling_control(lib)),
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
