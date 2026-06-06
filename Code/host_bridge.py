"""HostBridge - laptop-side counterpart of the LPC4370 firmware Backend.

The Backend block proper lives on the MCU as C firmware (see
`Firmware/Backend_LPC4370`). This module is the thin host-side bridge
that the Display block uses to talk to it: it serializes User-Input
commands into framed packets the firmware understands, and parses the
framed ADC data the firmware sends back.

    User Input ─cmd dict─▶ HostBridge.dispatch() ─▶ cmd_queue ─▶ Worker thread
                                                                  │
                                                                  ▼
                                                       (serialize + write
                                                        via pyserial transport
                                                        to the LPC4370)
    Display ◀─pump()─ data_queue ◀─(parse + scale)── Worker thread ◀─USB bytes

Two threads share a single Python process. The main thread runs the GUI
event loop and (a) calls HostBridge.dispatch() from the User Input callback
and (b) periodically calls HostBridge.pump() to deliver processed data to
the Display callback on the main thread.

Serial protocol (must match Firmware/Backend_LPC4370/src/proto.[ch]):

    Command packet (Host -> MCU)
      [0xAA] [cmd_id] [0..4 byte payload, MSB-first] [XOR checksum]
      Max packet size: 7 bytes.

    Data packet (MCU -> Host)
      [0xAA] [data_type] [count_hi] [count_lo] [N × 16-bit MSB-first samples] [XOR]
      data_type 0x80 = Ch1 ADC block, 0x81 = Ch2 ADC block.
"""

from __future__ import annotations

import queue
import struct
import threading
import time
from typing import Callable, Optional

import numpy as np


# --- Protocol constants ---------------------------------------------------

START_BYTE          = 0xAA
DATA_TYPE_CH1       = 0x80
DATA_TYPE_CH2       = 0x81

CMD_SET_TIMEBASE        = 0x01  # payload: uint32 ns/div, big-endian
CMD_SET_VDIV            = 0x02  # payload: uint32 µV/div, big-endian
CMD_SET_VOFFSET         = 0x03  # payload:  int32 µV,     big-endian
CMD_SET_TRIGGER_LEVEL   = 0x04  # payload:  int32 µV,     big-endian
CMD_SET_TRIGGER_MODE    = 0x05  # payload: 1 byte (0=rising, 1=falling, 2=auto)
CMD_SET_TRIGGER_SOURCE  = 0x06  # payload: 1 byte (1=Ch1, 2=Ch2)
CMD_SET_CHANNEL         = 0x07  # payload: 2 bytes (channel, enabled)
# 0x08 is free. The AC/DC coupling switch belongs here: add
# CMD_SET_COUPLING = 0x08 with a 2 byte payload (channel, dc_mode) and a
# matching PROTO_CMD_SET_COUPLING in proto.h. test_proto_parity.py checks the
# two stay byte for byte identical.
CMD_RUN                 = 0x10  # payload: none
CMD_STOP                = 0x11  # payload: none
CMD_SINGLE              = 0x12  # payload: none

TRIGGER_MODE_CODES   = {"rising": 0, "falling": 1, "auto": 2}
TRIGGER_SOURCE_CODES = {"Ch1": 1, "Ch2": 2}

# --- Hardware constants ---------------------------------------------------

BAUD                = 115200
SAMPLES_PER_BLOCK   = 4095          # firmware ADCHS_BLOCK_SAMPLES (GPDMA 12-bit cap)
ADC_BITS            = 12            # LPC4370 HSADC is 12-bit (0..4095)
ADC_MAX             = (1 << ADC_BITS) - 1   # = 4095
V_REF               = 3.3           # ADC reference (3.3 V rail)
V_REF_PER_DIV       = 0.5           # calibration constant: how many "raw"
                                    # volts per division at unit V/div setting
ADCHS_BASE_CLOCK_HZ = 68_000_000    # must match firmware adchs.h (68 MHz build)
# AFE voltage gain: ADC volts per input volt. Used to refer the displayed and
# measured voltage back to the BNC input. This is a single empirical number
# (about 3x measured: 300 mVpp in gives about 900 mVpp at the ADC) because the
# gain mux is not switching cleanly on the current board, so the effective gain
# is stuck no matter which cell we select. Once the mux switches for real,
# replace this constant with a per cell lookup keyed on the active gain: the
# firmware already exposes the ideal multipliers as AFE_GAIN_MULT[] in afe.c
# (0.256, 0.833, 2.564, 10), so scale by the current cell instead of a fixed 3.0
# and calibrate each cell against a known input.
AFE_GAIN = 3.0


# --- Helpers --------------------------------------------------------------

def xor_checksum(data: bytes) -> int:
    """Single-byte XOR over an arbitrary byte string."""
    c = 0
    for b in data:
        c ^= b
    return c & 0xFF


def serialize_command(cmd: dict) -> bytes:
    """Translate a User-Input command dict into framed byte packets.

    Most commands map to one packet (≤7 bytes). The User Input panel's
    "set_trigger" emits a single dict with level/mode/source all set, so
    here we expand it into THREE wire packets back-to-back so the firmware
    actually receives every field, not just the level.

    Returns b"" for unknown commands so the worker can silently skip them.
    """
    c = cmd.get("cmd")
    if c == "set_timebase":
        cid = CMD_SET_TIMEBASE
        payload = struct.pack(">I", _u32(cmd["value"] * 1e9))   # ns/div
    elif c == "set_vdiv":
        cid = CMD_SET_VDIV
        payload = struct.pack(">I", _u32(cmd["value"] * 1e6))   # µV/div
    elif c == "set_voffset":
        cid = CMD_SET_VOFFSET
        payload = struct.pack(">i", _i32(cmd["value"] * 1e6))   # µV
    elif c == "set_trigger":
        # Multi-field: emit 3 packets (level, mode, source). Mode/source
        # are optional in the dict - only emitted if present.
        out = b""
        out += _frame(CMD_SET_TRIGGER_LEVEL,
                      struct.pack(">i", _i32(cmd["level"] * 1e6)))
        if "mode" in cmd:
            out += _frame(CMD_SET_TRIGGER_MODE,
                          bytes([TRIGGER_MODE_CODES.get(cmd["mode"], 0)]))
        if "source" in cmd:
            out += _frame(CMD_SET_TRIGGER_SOURCE,
                          bytes([TRIGGER_SOURCE_CODES.get(cmd["source"], 1)]))
        return out
    elif c == "set_trigger_mode":
        cid = CMD_SET_TRIGGER_MODE
        payload = bytes([TRIGGER_MODE_CODES.get(cmd["mode"], 0)])
    elif c == "set_trigger_source":
        cid = CMD_SET_TRIGGER_SOURCE
        payload = bytes([TRIGGER_SOURCE_CODES.get(cmd["source"], 1)])
    elif c == "set_channel":
        cid = CMD_SET_CHANNEL
        payload = bytes([int(cmd["channel"]) & 0xFF,
                         1 if cmd["enabled"] else 0])
    # A "set_coupling" branch slots in here once the AC/DC switch is wired:
    #     elif c == "set_coupling":
    #         cid = CMD_SET_COUPLING
    #         payload = bytes([int(cmd["channel"]) & 0xFF,
    #                          1 if cmd["dc"] else 0])
    # mirroring the SET_CHANNEL shape above and the proto.h ID.
    elif c == "run":
        cid, payload = CMD_RUN, b""
    elif c == "stop":
        cid, payload = CMD_STOP, b""
    elif c == "single":
        cid, payload = CMD_SINGLE, b""
    else:
        return b""

    return _frame(cid, payload)


def _frame(cid: int, payload: bytes) -> bytes:
    """Wrap one (cmd_id, payload) into the on-wire framing."""
    body = bytes([START_BYTE, cid]) + payload
    return body + bytes([xor_checksum(body)])


def _u32(x: float) -> int:
    """Clamp + cast to unsigned 32-bit."""
    v = int(round(x))
    return max(0, min(0xFFFFFFFF, v))


def _i32(x: float) -> int:
    """Clamp + cast to signed 32-bit."""
    v = int(round(x))
    return max(-(1 << 31), min((1 << 31) - 1, v))


# --- Backend -------------------------------------------------------------

class HostBridge:
    """Laptop-side bridge to the LPC4370 firmware Backend.

    Construct with either a real `serial.Serial` instance or a MockMCU
    (anything with `read(n)` / `write(bytes)` methods). Call `start()` to
    spin up the worker thread, register the display callback, and call
    `pump()` from the main thread on a regular cadence to deliver data.
    """

    def __init__(self, transport, *, log_packets: bool = False,
                 verify_checksums: bool = False, debug_threads: bool = False):
        self._transport = transport
        self._cmd_queue: "queue.Queue[dict]" = queue.Queue()
        self._data_queue: "queue.Queue[dict]" = queue.Queue()
        self._display_callback: Optional[Callable[[dict], None]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Diagnostic logs (populated when the corresponding flag is set).
        self._log_packets = log_packets
        self._verify_checksums = verify_checksums
        self._debug_threads = debug_threads
        self.packet_log: list[tuple[dict, bytes]] = []
        self.checksum_log: list[tuple[bool, int, int]] = []  # (ok, calc, tx)
        self.error_log: list[str] = []

        # Locally-tracked display settings (used to scale incoming samples).
        self._vdiv = 1.0
        self._timebase = 1e-3
        self._sample_rate = SAMPLES_PER_BLOCK / (10 * self._timebase)

    # ---- public API ----------------------------------------------------

    def register_display_callback(self, cb: Callable[[dict], None]) -> None:
        """Display block calls this to receive {time, voltage, channel} dicts."""
        self._display_callback = cb

    def dispatch(self, cmd: dict) -> None:
        """User Input callback target. Updates local state and enqueues."""
        c = cmd.get("cmd")
        if c == "set_vdiv":
            self._vdiv = float(cmd["value"])
        elif c == "set_timebase":
            self._timebase = float(cmd["value"])
            self._sample_rate = SAMPLES_PER_BLOCK / max(10 * self._timebase, 1e-9)
        self._cmd_queue.put(cmd)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker, name="BackendWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        # Best-effort transport close
        close = getattr(self._transport, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def pump(self) -> int:
        """Drain pending Display callbacks on the calling thread.

        Returns the number of records dispatched. Call this from the GUI
        main loop (e.g. tkinter `root.after()` or Qt `QTimer.timeout`) so
        that callbacks run on the main thread - matching the doc's
        `root.after(0, callback, data)` pattern.
        """
        n = 0
        while True:
            try:
                data = self._data_queue.get_nowait()
            except queue.Empty:
                break
            if self._debug_threads:
                data["_thread"] = threading.current_thread().name
            if self._display_callback is not None:
                self._display_callback(data)
            n += 1
        return n

    # ---- worker thread -------------------------------------------------

    def _worker(self) -> None:
        rx_buf = bytearray()
        while self._running:
            self._drain_cmd_queue()
            chunk = self._safe_read(4096)
            if chunk:
                rx_buf.extend(chunk)
                self._consume(rx_buf)
            else:
                time.sleep(0.001)

    def _drain_cmd_queue(self) -> None:
        try:
            while True:
                cmd = self._cmd_queue.get_nowait()
                pkt = serialize_command(cmd)
                if not pkt:
                    continue
                if self._log_packets:
                    self.packet_log.append((cmd, pkt))
                if self._verify_checksums:
                    calc = xor_checksum(pkt[:-1])
                    self.checksum_log.append((calc == pkt[-1], calc, pkt[-1]))
                try:
                    self._transport.write(pkt)
                except Exception as e:
                    self.error_log.append(f"transport.write failed: {e}")
        except queue.Empty:
            pass

    def _safe_read(self, n: int) -> bytes:
        try:
            return self._transport.read(n) or b""
        except Exception as e:
            self.error_log.append(f"transport.read failed: {e}")
            return b""

    def _consume(self, buf: bytearray) -> None:
        """Pull every complete packet out of `buf` and dispatch it."""
        while True:
            consumed = self._try_parse_one(buf)
            if consumed == 0:
                return
            del buf[:consumed]

    def _try_parse_one(self, buf: bytearray) -> int:
        # Hunt for a start byte
        i = 0
        while i < len(buf) and buf[i] != START_BYTE:
            i += 1
        if i:
            del buf[:i]
        if len(buf) < 4:
            return 0

        dtype = buf[1]
        if dtype not in (DATA_TYPE_CH1, DATA_TYPE_CH2):
            # Unknown type - skip past the start byte and try again.
            self.error_log.append(f"unknown data type 0x{dtype:02X}")
            return 1

        count = (buf[2] << 8) | buf[3]
        if count == 0 or count > SAMPLES_PER_BLOCK:
            self.error_log.append(f"bad sample count {count}")
            return 1
        total = 4 + count * 2 + 1
        if len(buf) < total:
            return 0  # need more bytes

        packet = bytes(buf[:total])
        body, rx_cksum = packet[:-1], packet[-1]
        calc = xor_checksum(body)
        if calc != rx_cksum:
            self.error_log.append(
                f"checksum mismatch (calc 0x{calc:02X} vs rx 0x{rx_cksum:02X})")
            return total  # consume but discard

        # Parse samples and convert to voltage. Use REAL ADC volts, AC-coupled
        # (subtract the block mean so the trace centres at 0). Crucially this does
        # NOT scale by V/div - the data must be in fixed volts and only the plot's
        # y-range scales with V/div (main.py _v_max), otherwise the two cancel and
        # changing V/div does nothing on screen. Centring at 0 also means the
        # default 0 V trigger level sits right on the signal.
        raw = struct.unpack(f">{count}H", body[4:])
        counts = np.asarray(raw, dtype=np.uint16) & ADC_MAX
        cf = counts.astype(np.float64)
        voltage = (cf - cf.mean()) / ADC_MAX * V_REF / AFE_GAIN
        # Build the time axis from the ACTUAL sample period, replicating the
        # firmware's divider math (main.c SET_TIMEBASE). This stays correct even
        # at fast timebases where the divider clamps to 1 (rate caps at the ADC
        # max), so the displayed window/frequency matches reality rather than the
        # requested timebase.
        ns_per_div = self._timebase * 1e9
        denom = SAMPLES_PER_BLOCK * 1e8
        div = round(ADCHS_BASE_CLOCK_HZ * ns_per_div / denom) if denom else 1
        div = max(1, min(65535, div))
        dt = div / ADCHS_BASE_CLOCK_HZ          # seconds per sample
        time_axis = np.arange(count, dtype=np.float64) * dt
        channel = 1 if dtype == DATA_TYPE_CH1 else 2

        self._data_queue.put({
            "time": time_axis,
            "voltage": voltage,
            "channel": channel,
            "samples_raw": counts,  # included for Test 4 verification
        })
        return total
