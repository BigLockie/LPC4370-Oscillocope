"""MockMCU - pyserial-compatible stand-in for the LPC4370.

Implements `read(n)` / `write(bytes)` / `close()` so the Backend can talk
to it via dependency injection without real hardware. Generates ADC sample
blocks matching the protocol described in the Block 2 design doc:

    [0xAA] [data_type] [count_hi] [count_lo] [N × 16-bit MSB-first samples] [XOR]

Used by:
  - The integrated display (`Code/main.py`) for an end-to-end demo
  - Tests 4, 5, 6 in `Code/tests/test_backend.py`
"""

from __future__ import annotations

import struct
import threading
import time
from typing import Iterable, Optional

import numpy as np

from host_bridge import (
    START_BYTE, DATA_TYPE_CH1, DATA_TYPE_CH2,
    SAMPLES_PER_BLOCK, ADC_MAX, xor_checksum,
    CMD_SET_TRIGGER_LEVEL, CMD_SET_TRIGGER_MODE, CMD_SET_TRIGGER_SOURCE,
    CMD_SET_TIMEBASE, CMD_SET_VDIV, CMD_SET_VOFFSET,
    CMD_SET_CHANNEL, CMD_RUN, CMD_STOP, CMD_SINGLE,
)


# Payload-length lookup. Mirrors proto.c's payload_len_for(); used by the
# in-MCU command parser to slice arbitrary input streams.
_PLEN = {
    CMD_SET_TIMEBASE: 4, CMD_SET_VDIV: 4, CMD_SET_VOFFSET: 4,
    CMD_SET_TRIGGER_LEVEL: 4, CMD_SET_TRIGGER_MODE: 1,
    CMD_SET_TRIGGER_SOURCE: 1, CMD_SET_CHANNEL: 2,
    CMD_RUN: 0, CMD_STOP: 0, CMD_SINGLE: 0,
}


class MockMCU:
    """Fake MCU that speaks the FastOscilloscope serial protocol.

    Modes:
      - "sine"  : continuous bipolar sine on Ch1 (default)
      - "dual"  : sine on Ch1 + cosine on Ch2 at different frequencies
      - "ramp"  : 0..1023 ramp on Ch1 (used by Test 4)
      - "idle"  : no auto-generation; call .send_block() manually
    """

    def __init__(self, mode: str = "dual",
                 samples_per_block: int = SAMPLES_PER_BLOCK,
                 block_period_s: float = 0.016):
        # block_period_s controls how often the mock thread pushes a new
        # block into the queue. 0.016s ≈ 60 Hz per channel ≈ 60 FPS in the
        # GUI. Tests that prefer a slower/more deterministic cadence pass
        # block_period_s explicitly.
        self._tx_buf = bytearray()      # MCU -> Backend (Backend.read consumes)
        self._rx_buf = bytearray()      # Backend -> MCU (write side, ignored)
        self._lock = threading.Lock()

        self._mode = mode
        self._n = samples_per_block
        self._period = block_period_s
        self._t_phase = 0.0
        self._inject_corrupt = False

        # Mock signal generator state (mutable via set_mock_signal). Defaults
        # match the QDoubleSpinBox defaults in user_input_qt.py so the GUI
        # and MockMCU agree before the user touches anything.
        self._ch1_freq_khz = 5.0
        self._ch2_freq_khz = 15.0
        self._ch1_amp_v    = 5.0
        self._ch2_amp_v    = 5.0
        # Fixed nominal sample rate the mock pretends to capture at. With
        # 1024 samples/block at 1 MSa/s, a 5 kHz sine produces ~5 cycles
        # per block - visually similar to the old hardcoded 0.05 rad/sample.
        self._mock_sample_rate = 1.0e6
        # Voltage that maps to a full-scale ADC count (the AFE's nominal
        # input range). Used to convert amp_v -> ADC counts.
        self._mock_full_scale_v = 15.0

        # Trigger state - updated when the mock parses incoming
        # SET_TRIGGER_LEVEL/MODE/SOURCE packets. Defaults: rising-edge,
        # level=0V on Ch1 (the standard zero-crossing trigger).
        self._trig_level_v = 0.0
        self._trig_mode    = "rising"   # "rising" / "falling" / "auto"
        self._trig_source  = 1          # 1 or 2
        self._trig_mode_codes   = {0: "rising", 1: "falling", 2: "auto"}

        # Tiny embedded command parser state (mirrors proto.c's state machine).
        self._parse_state    = 0    # 0=IDLE, 1=CMD, 2=PAY, 3=SUM
        self._parse_id       = 0
        self._parse_plen     = 0
        self._parse_payload  = bytearray()
        self._parse_xor      = 0

        self._running = True
        if mode != "idle":
            self._gen = threading.Thread(
                target=self._loop, name="MockMCU", daemon=True)
            self._gen.start()
        else:
            self._gen = None

    # ---- pyserial-compatible interface --------------------------------

    def write(self, data: bytes) -> int:
        with self._lock:
            self._rx_buf.extend(data)
        return len(data)

    def read(self, n: int) -> bytes:
        with self._lock:
            chunk = bytes(self._tx_buf[:n])
            del self._tx_buf[:n]
        return chunk

    def close(self) -> None:
        self._running = False

    # ---- inspection (used by tests) -----------------------------------

    @property
    def received(self) -> bytes:
        """Bytes the Backend has written to the MCU since boot."""
        with self._lock:
            return bytes(self._rx_buf)

    def clear_received(self) -> None:
        with self._lock:
            self._rx_buf.clear()

    # ---- inbound command parser ---------------------------------------

    def _drain_and_parse_rx(self) -> None:
        """Consume any bytes in _rx_buf and update trigger state from
        completed packets. Mirrors proto.c's proto_parse_byte() logic so
        the laptop demo behaves like the firmware will."""
        with self._lock:
            chunk = bytes(self._rx_buf)
            self._rx_buf.clear()
        for b in chunk:
            self._parse_one_byte(b)

    def _parse_one_byte(self, b: int) -> None:
        s = self._parse_state
        if s == 0:                           # IDLE
            if b == START_BYTE:
                self._parse_xor     = b
                self._parse_payload = bytearray()
                self._parse_state   = 1
        elif s == 1:                         # CMD
            if b not in _PLEN:
                self._parse_state = 0        # unknown command -> resync
                return
            self._parse_id    = b
            self._parse_plen  = _PLEN[b]
            self._parse_xor  ^= b
            self._parse_state = 3 if self._parse_plen == 0 else 2
        elif s == 2:                         # PAY
            self._parse_payload.append(b)
            self._parse_xor ^= b
            if len(self._parse_payload) >= self._parse_plen:
                self._parse_state = 3
        elif s == 3:                         # SUM
            if b == self._parse_xor:
                self._handle_command(self._parse_id, bytes(self._parse_payload))
            self._parse_state = 0            # always return to IDLE

    def _handle_command(self, cid: int, payload: bytes) -> None:
        if cid == CMD_SET_TRIGGER_LEVEL:
            uv = struct.unpack(">i", payload)[0]
            with self._lock:
                self._trig_level_v = uv / 1e6
        elif cid == CMD_SET_TRIGGER_MODE:
            with self._lock:
                self._trig_mode = self._trig_mode_codes.get(payload[0], "rising")
        elif cid == CMD_SET_TRIGGER_SOURCE:
            with self._lock:
                self._trig_source = 1 if payload[0] == 1 else 2
        # All other commands are silently dropped - MockMCU doesn't model
        # timebase, channel-enable, or run/stop semantics yet.

    # ---- test injection -----------------------------------------------

    def send_block(self, samples: Iterable[int],
                   *, channel: int = 1, corrupt: bool = False) -> None:
        """Push exactly one ADC block onto the TX buffer immediately.

        Used by Tests 4 and 5 to inject known-good and known-bad packets.
        """
        dtype = DATA_TYPE_CH1 if channel == 1 else DATA_TYPE_CH2
        pkt = self._frame(samples, dtype)
        if corrupt:
            pkt = pkt[:-1] + bytes([pkt[-1] ^ 0xFF])
        with self._lock:
            self._tx_buf.extend(pkt)

    def inject_corrupt_next(self) -> None:
        """Make the next auto-generated block carry a bad checksum."""
        self._inject_corrupt = True

    # ---- internal -----------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            self._drain_and_parse_rx()
            self._emit_one_block()
            time.sleep(self._period)

    def set_mock_signal(self, ch1_freq_khz: float, ch2_freq_khz: float,
                        ch1_amp_v: float, ch2_amp_v: float) -> None:
        """Update the synthesized waveform's frequency/amplitude on the fly.

        Called by main.OscilloscopeUI when the user moves the Mock Signal
        Generator knobs. Thread-safe - _emit_one_block runs on the worker
        thread and reads these under the same lock.
        """
        with self._lock:
            self._ch1_freq_khz = float(ch1_freq_khz)
            self._ch2_freq_khz = float(ch2_freq_khz)
            self._ch1_amp_v    = float(ch1_amp_v)
            self._ch2_amp_v    = float(ch2_amp_v)

    def _emit_one_block(self) -> None:
        if self._mode == "ramp":
            samples1 = np.arange(self._n, dtype=np.uint16) % (ADC_MAX + 1)
            self._push(samples1, DATA_TYPE_CH1, self._inject_corrupt)
            self._inject_corrupt = False
            return

        # Snapshot the mutable knobs once so a mid-block change can't tear.
        with self._lock:
            f1 = self._ch1_freq_khz
            f2 = self._ch2_freq_khz
            a1 = self._ch1_amp_v
            a2 = self._ch2_amp_v
            sr = self._mock_sample_rate
            fs = self._mock_full_scale_v
            t_level = self._trig_level_v
            t_mode  = self._trig_mode
            t_src   = self._trig_source

        x  = np.arange(self._n)
        center = ADC_MAX / 2.0

        # rad / sample = 2π·f_hz / sample_rate
        omega1 = 2.0 * np.pi * (f1 * 1e3) / sr
        omega2 = 2.0 * np.pi * (f2 * 1e3) / sr

        # Pick starting phases: trigger-locked if the source amplitude can
        # reach the requested level and we're in rising/falling; otherwise
        # free-run from the rolling _t_phase. Both channels use phases
        # derived from the SAME trigger time so their relative timing is
        # consistent (Ch2 will look stable iff its frequency is harmonically
        # related to Ch1, just like a real scope).
        ph1, ph2 = self._compute_trigger_phases(
            t_level, t_mode, t_src, a1, a2, f1, f2,
        )

        amp1_cnt = (a1 / fs) * (ADC_MAX / 2.0)
        sine1    = np.sin(x * omega1 + ph1) * amp1_cnt + center
        samples1 = np.clip(sine1, 0, ADC_MAX).astype(np.uint16)
        self._push(samples1, DATA_TYPE_CH1, self._inject_corrupt)

        if self._mode == "dual":
            amp2_cnt = (a2 / fs) * (ADC_MAX / 2.0)
            cos2     = np.cos(x * omega2 + ph2) * amp2_cnt + center
            samples2 = np.clip(cos2, 0, ADC_MAX).astype(np.uint16)
            self._push(samples2, DATA_TYPE_CH2, False)

        self._inject_corrupt = False
        self._t_phase += 0.2

    def _compute_trigger_phases(self, level_v, mode, source,
                                a1, a2, f1, f2):
        """Return (ph1, ph2) - the starting phases for Ch1 (sine) and Ch2
        (cosine) such that the SOURCE channel's first sample matches the
        trigger condition. Falls back to the rolling _t_phase for
        free-running behavior in 'auto' mode or when the level is
        unreachable."""

        # Auto mode -> free-run, completely ignore trigger.
        if mode == "auto":
            return self._t_phase, self._t_phase * 0.5

        # Source amplitude in volts (the channel we're triggering on).
        a_src = a1 if source == 1 else a2
        if a_src <= 0:
            return self._t_phase, self._t_phase * 0.5

        # Normalized level: -1 .. +1 of the source channel's amplitude.
        norm = level_v / a_src
        if not (-1.0 <= norm <= 1.0):
            # Trigger condition unreachable -> free-run rather than freezing.
            return self._t_phase, self._t_phase * 0.5

        # Compute trigger phase for the source channel.
        if source == 1:
            # Ch1 = sin(ph) on rising -> ph = asin(norm)   (cos > 0)
            #              on falling -> ph = π − asin(norm)
            asin_v = np.arcsin(norm)
            ph_src = asin_v if mode == "rising" else (np.pi - asin_v)
            ph1    = ph_src
            # Ch2 phase at the same instant: t_trig = ph1 / omega1, so
            # cosine arg at t_trig = omega2 * t_trig = ph1 * f2 / f1.
            ph2    = ph1 * (f2 / f1) if f1 > 0 else 0.0
        else:
            # Source = Ch2 (cosine): on rising slope (-sin > 0) -> sin < 0
            #   ph = -acos(norm)    (cos = norm, sin ≤ 0 -> -sin ≥ 0)
            # on falling slope:
            #   ph = +acos(norm)    (cos = norm, sin ≥ 0 -> -sin ≤ 0)
            acos_v = np.arccos(norm)
            ph_src = -acos_v if mode == "rising" else acos_v
            ph2    = ph_src
            ph1    = ph2 * (f1 / f2) if f2 > 0 else 0.0

        return ph1, ph2

    def _push(self, samples, dtype: int, corrupt: bool) -> None:
        pkt = self._frame(samples, dtype)
        if corrupt:
            pkt = pkt[:-1] + bytes([pkt[-1] ^ 0xFF])
        with self._lock:
            self._tx_buf.extend(pkt)

    @staticmethod
    def _frame(samples, dtype: int) -> bytes:
        arr = np.asarray(list(samples), dtype=np.uint16) & ADC_MAX
        count = len(arr)
        header = bytes([START_BYTE, dtype,
                        (count >> 8) & 0xFF, count & 0xFF])
        body = header + arr.byteswap().tobytes() if arr.dtype.byteorder == "<" \
               else header + arr.tobytes()
        # numpy's default is platform-endian; force big-endian explicitly
        body = header + struct.pack(f">{count}H", *(int(s) for s in arr))
        return body + bytes([xor_checksum(body)])
