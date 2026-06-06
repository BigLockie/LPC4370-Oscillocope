"""FastOscilloscope main window.

Plot area on the left, UserInputPanelQt on the right. The control panel
emits command dicts; they go (a) to a local handler that updates display
state and (b) into the Backend which packetizes them onto the USB serial
link to the MCU. The Backend's worker thread parses incoming ADC blocks,
converts counts -> volts, and pushes records back through the main-thread
pump for plotting.

Without a MCU connected, a MockMCU stands in for the hardware so the demo
runs end-to-end on any laptop.
"""

import sys
import time
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
)
from PyQt5.QtCore import QTimer

from host_bridge import (HostBridge, SAMPLES_PER_BLOCK, ADCHS_BASE_CLOCK_HZ,
                         ADC_MAX, V_REF, AFE_GAIN)
from mock_mcu import MockMCU
from user_input_qt import UserInputPanelQt


class OscilloscopeUI(QMainWindow):

    # Standard scope: 10 horizontal divs, 10 vertical divs.
    H_DIVS = 10
    V_DIVS = 10

    def __init__(self, port: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("FastOscilloscope - LPC4370")
        self.resize(1280, 720)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1e1e1e; color: #e0e0e0; }
            QGroupBox {
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                margin-top: 8px;
                padding: 8px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #b0b0b0;
            }
            QPushButton {
                background-color: #2d2d2d;
                border: 1px solid #4a4a4a;
                border-radius: 3px;
                padding: 5px 10px;
            }
            QPushButton:hover  { background-color: #3a3a3a; }
            QPushButton:pressed{ background-color: #1a1a1a; }
            QComboBox, QLineEdit, QDoubleSpinBox {
                background-color: #2d2d2d;
                border: 1px solid #4a4a4a;
                padding: 3px;
            }
            QLabel { background: transparent; }
        """)
        pg.setConfigOptions(useOpenGL=True, antialias=False,
                            background="#000000", foreground="#b0b0b0")

        # --- display state ---
        self._timebase = 5e-5
        self._vdiv = 3.0
        self._voffset = 0.0
        self._running = True
        self._single_pending = False

        # --- central layout ---
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        status = QHBoxLayout()
        self.scale_lbl = QLabel()
        self.fps_lbl = QLabel("FPS: 0.0")
        self.fps_lbl.setStyleSheet("color: #00FF88;")
        self.state_lbl = QLabel("RUN")
        self.state_lbl.setStyleSheet("color: #00FF88; font-weight: bold;")
        self.backend_lbl = QLabel()
        self.backend_lbl.setStyleSheet("color: #888888;")
        self.meas_lbl = QLabel("")          # per-channel measurements (freq / Vpp / Vdc)
        self.meas_lbl.setStyleSheet("color: #FFD080;")
        status.addWidget(self.state_lbl)
        status.addSpacing(12)
        status.addWidget(self.scale_lbl)
        status.addSpacing(12)
        status.addWidget(self.backend_lbl)
        status.addSpacing(16)
        status.addWidget(self.meas_lbl)
        status.addStretch(1)
        status.addWidget(self.fps_lbl)
        root.addLayout(status)

        body = QHBoxLayout()
        body.setSpacing(8)
        root.addLayout(body, 1)

        self.graph = pg.PlotWidget()
        self.graph.showGrid(x=True, y=True, alpha=0.35)
        self.graph.setLabel("left", "Voltage", units="V")
        self.graph.setLabel("bottom", "Time", units="s")
        self.graph.disableAutoRange()
        self.graph.setMouseEnabled(x=False, y=False)
        self.graph.hideButtons()
        self.graph.setMenuEnabled(False)
        body.addWidget(self.graph, 1)

        self.controls = UserInputPanelQt()
        self.controls.setFixedWidth(340)
        self.controls.register_callback(self._handle_command)
        body.addWidget(self.controls, 0)

        self._ch1_line = self.graph.plot(pen=pg.mkPen(color=(0, 140, 220), width=2))
        self._ch2_line = self.graph.plot(pen=pg.mkPen(color=(220, 120, 180), width=2))
        for line in (self._ch1_line, self._ch2_line):
            line.setClipToView(True)
            # Draw the actual samples connected by straight lines (linear
            # interpolation). 'peak' downsampling drew a min/max envelope that
            # looked like a filled band for dense signals; with 4095 samples per
            # block pyqtgraph renders all points fine (OpenGL is enabled).
            line.setDownsampling(ds=False)
        self._ch_visible = {1: True, 2: True}

        # --- software trigger state (host-side) ---
        # The firmware free-runs (no hardware trigger gating), so we align each
        # incoming block here: find the trigger edge on the source channel and
        # roll every channel by the same offset so the trace locks horizontally.
        self._trig_level_v = 0.0
        self._trig_mode    = "rising"   # "rising" | "falling" | "auto"
        self._trig_source  = 1          # 1 = Ch1, 2 = Ch2
        self._trig_offset  = 0          # last good trigger index (held on no-edge)

        # Latest per-channel measurements: {ch: (freq_hz|None, vpp_v, vrms_v)}.
        self._meas = {1: None, 2: None}
        # Latest raw block per channel: {ch: (samples_raw, time)}. We stash these
        # on every block but only run the (expensive) FFT measurement at the label
        # cadence (~2 Hz) in _tick, instead of once per block. The measurement is
        # only displayed twice a second, so per-block FFTs were pure wasted work.
        self._last_raw = {1: None, 2: None}

        # --- backend ---
        self._mock_mcu: Optional[MockMCU] = None
        if port:
            try:
                import serial  # type: ignore
                transport = serial.Serial(port, 115200, timeout=0.1)
                self.backend_lbl.setText(f"Backend: serial {port}")
            except Exception as e:
                print(f"[main] could not open {port}: {e} - falling back to MockMCU")
                self._mock_mcu = MockMCU(mode="dual")
                transport = self._mock_mcu
                self.backend_lbl.setText("Backend: MockMCU (fallback)")
        else:
            self._mock_mcu = MockMCU(mode="dual")
            transport = self._mock_mcu
            self.backend_lbl.setText("Backend: MockMCU")

        self.bridge = HostBridge(transport)
        self.bridge.register_display_callback(self._on_backend_data)
        self.bridge.start()

        self._push_scale()

        # Kick the hardware into a known streaming state so the display matches
        # the default RUN indicator. The control panel checks CH1/CH2 in its
        # __init__ (setChecked) *before* register_callback is wired, so those
        # initial enables are dropped - and nothing ever auto-sends Run. Without
        # this, the firmware sits idle and the trace is frozen even though the
        # status bar shows "RUN". Send the enables + current timebase + run now.
        for ch in (1, 2):
            self.bridge.dispatch({"cmd": "set_channel", "channel": ch, "enabled": True})
        self.bridge.dispatch({"cmd": "set_timebase", "value": self._timebase})
        self.bridge.dispatch({"cmd": "run"})

        # Pump display + tick FPS at ~60 Hz on the GUI thread
        self._last_time = time.time()
        self._frames = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    # ---------- scale / state helpers ----------

    def _v_max(self):
        return self._vdiv * (self.V_DIVS / 2)

    def _window(self):
        # Displayed window = the requested timebase (10 divs). We deep-capture
        # more than this at fast timebases (the ADC clamps to its max rate, so the
        # block actually spans longer), but we only DISPLAY the requested span so
        # the user sees the zoom they asked for (e.g. ~10 cycles at 1 µs/div, not
        # the full ~60). The per-sample time axis (host_bridge) stays accurate, so
        # frequency still reads correctly; the extra captured samples sit off the
        # right edge.
        return self._timebase * self.H_DIVS

    def _push_scale(self):
        v_max = self._v_max()
        window = self._window()
        self.graph.setYRange(-v_max + self._voffset, v_max + self._voffset, padding=0)
        self.graph.setXRange(0, window, padding=0)
        self.scale_lbl.setText(
            f"{self._vdiv:g} V/div  |  {window * 1e6:.1f} us window"
            if window < 1e-3 else
            f"{self._vdiv:g} V/div  |  {window * 1e3:.2f} ms window"
        )

    def _set_state(self, running: bool):
        self._running = running
        self.state_lbl.setText("RUN" if running else "STOP")
        self.state_lbl.setStyleSheet(
            "color: #00FF88; font-weight: bold;" if running
            else "color: #FF6B6B; font-weight: bold;"
        )

    # ---------- command dispatch ----------

    def _handle_command(self, cmd: dict):
        """Local view + forward to Backend. Display-only fields stay local."""
        c = cmd.get("cmd")
        if c == "set_timebase":
            self._timebase = float(cmd["value"])
            self._push_scale()
        elif c == "set_vdiv":
            self._vdiv = float(cmd["value"])
            self._push_scale()
        elif c == "set_voffset":
            self._voffset = float(cmd["value"])
            self._push_scale()
        elif c == "set_trigger":
            # Track trigger params locally for the host-side software trigger
            # (still forwarded to the firmware below - harmless if it free-runs).
            self._trig_level_v = float(cmd.get("level", self._trig_level_v))
            if "mode" in cmd:
                self._trig_mode = cmd["mode"]
            if "source" in cmd:
                self._trig_source = 2 if cmd["source"] in ("Ch2", 2) else 1
        elif c == "set_channel":
            ch = int(cmd["channel"]); on = bool(cmd["enabled"])
            self._ch_visible[ch] = on
            (self._ch1_line if ch == 1 else self._ch2_line).setVisible(on)
        elif c == "run":
            self._set_state(True)
        elif c == "stop":
            self._set_state(False)
        elif c == "single":
            self._single_pending = True
            self._set_state(True)
        elif c == "set_mock_signal":
            # No corresponding hardware command - only meaningful with MockMCU.
            # Apply directly to the mock generator if we have one, then exit
            # without forwarding (real Backend would just ignore the dict).
            if self._mock_mcu is not None:
                self._mock_mcu.set_mock_signal(
                    cmd["ch1_freq_khz"], cmd["ch2_freq_khz"],
                    cmd["ch1_amp_v"],    cmd["ch2_amp_v"],
                )
            return

        # Forward everything else to the Backend (which packetizes it).
        self.bridge.dispatch(cmd)

    # ---------- data pump ----------

    @staticmethod
    def _find_trigger(v, level, mode, auto_level=False):
        """Index of the first real edge crossing `level` (rising/falling), or None.

        The displayed signal is AC-coupled (mean-subtracted, input-referred), so
        it's centred on 0 V and `level` is read literally against it: a level the
        signal never reaches returns None, so the trace free-runs instead of
        locking - that's what makes the Level control actually do something.

        `auto_level` (the panel's "auto" Mode) restores the old hands-off
        behaviour: an out-of-band level snaps to the signal's MIDPOINT so it
        always locks without tuning. Hysteresis (10% of span past the opposite
        side) keeps a single noisy sample from false-triggering in either mode.
        """
        n = v.size
        if n < 4:
            return None
        vmin = float(v.min()); vmax = float(v.max()); span = vmax - vmin
        if span < 1e-6:
            return None                       # flat line - nothing to trigger on
        if auto_level:
            lo, hi = vmin + 0.15 * span, vmax - 0.15 * span
            lvl = level if (lo <= level <= hi) else 0.5 * (vmin + vmax)
        else:
            lvl = level                        # literal - out of band => no lock
        hys = 0.10 * span
        if mode == "falling":
            armed = v > (lvl + hys)
            if not armed.any():
                return None
            start = int(np.argmax(armed))
            seg = v[start:]
            cr = np.nonzero((seg[:-1] >= lvl) & (seg[1:] < lvl))[0]
        else:                                  # rising (and auto)
            armed = v < (lvl - hys)
            if not armed.any():
                return None
            start = int(np.argmax(armed))
            seg = v[start:]
            cr = np.nonzero((seg[:-1] <= lvl) & (seg[1:] > lvl))[0]
        if not cr.size:
            return None
        # Sub-sample crossing: linear-interpolate the exact point where the signal
        # crosses `lvl` between samples k and k+1. Returning a FRACTIONAL index
        # removes the ±1-sample phase jitter that makes a fast trace shimmer.
        k = start + int(cr[0])
        y0 = float(v[k]); y1 = float(v[k + 1])
        frac = 0.0 if (y1 == y0) else (lvl - y0) / (y1 - y0)
        return k + frac

    @staticmethod
    def _measure(counts, t):
        """Measure (frequency_Hz|None, Vpp, Vrms) from one raw-count block.

        Vpp/Vrms are real volts at the ADC. Frequency is the dominant spectral
        peak (FFT) over the accurate per-sample time base - robust to noise.
        """
        n = counts.size
        if n < 8:
            return (None, 0.0, 0.0)
        c = counts.astype(np.float64)
        cmax = float(c.max()); cmin = float(c.min()); cmean = float(c.mean())
        # Refer to the BNC input by de-embedding the AFE gain (same as the display).
        vpp = (cmax - cmin) / ADC_MAX * V_REF / AFE_GAIN
        vrms = float(c.std()) / ADC_MAX * V_REF / AFE_GAIN
        freq = None
        if (cmax - cmin) > 20 and t.size > 1:
            dt = float(t[1] - t[0])
            if dt > 0:
                spec = np.abs(np.fft.rfft(c - cmean))
                if spec.size > 1:
                    spec[0] = 0.0
                    k = int(np.argmax(spec))
                    if k > 0:
                        freq = k * (1.0 / dt) / n
        return (freq, vpp, vrms)

    @staticmethod
    def _fmt_freq(f):
        if f is None:
            return "-- Hz"
        if f >= 1e6:
            return f"{f / 1e6:.3f} MHz"
        if f >= 1e3:
            return f"{f / 1e3:.2f} kHz"
        return f"{f:.0f} Hz"

    @staticmethod
    def _fmt_v(v):
        return f"{v * 1e3:.0f} mV" if abs(v) < 1.0 else f"{v:.2f} V"

    def _update_meas_label(self):
        parts = []
        for ch, name in ((1, "Ch1"), (2, "Ch2")):
            if not self._ch_visible[ch]:
                continue
            m = self._meas.get(ch)
            if m is None:
                continue
            freq, vpp, vrms = m
            parts.append(f"{name}: {self._fmt_freq(freq)}  "
                         f"{self._fmt_v(vpp)}pp  {self._fmt_v(vrms)}rms")
        self.meas_lbl.setText("    ".join(parts))

    def _on_backend_data(self, data: dict):
        """Called on the main thread by Backend.pump()."""
        if not self._running:
            return
        ch = data["channel"]
        v = np.asarray(data["voltage"])
        t = np.asarray(data["time"])

        # Stash the raw block for measurement. The FFT runs in _tick at the label
        # cadence (~2 Hz), NOT here on every block - see _last_raw above.
        self._last_raw[ch] = (np.asarray(data["samples_raw"]), t)

        # Host-side software trigger (the firmware free-runs). The two channels are
        # captured in ALTERNATION (not simultaneously), so phase between them is
        # meaningless anyway - we align EACH channel to its OWN edge. This makes a
        # clean channel lock regardless of the 'Source' dropdown (so pointing
        # Source at the dead/oscillating CH1 can't stop CH2 from locking, which was
        # the bug). 'auto'/rising find a rising edge, 'falling' a falling one; a
        # trace with no real edge (oscillating CH1, or a 1 MHz signal aliased at a
        # slow timebase) finds nothing and just free-runs.
        # "auto" Mode = hands-off auto-level (always locks). "rising"/"falling"
        # use the Level literally, so an out-of-reach level stops the lock.
        auto_level = (self._trig_mode == "auto")
        mode = self._trig_mode if self._trig_mode in ("rising", "falling") else "rising"
        pos = self._find_trigger(v, self._trig_level_v, mode, auto_level)
        if pos is not None and v.size > 1:
            # Shift the time axis so the (sub-sample) crossing sits at t=0. We
            # shift rather than slice so the alignment is fractional - this kills
            # the ±1-sample shimmer. Pre-trigger samples land at t<0 and are
            # clipped off the left by the [0, window] x-range.
            dt = float(t[1] - t[0])
            t = t - pos * dt

        if ch == 1 and self._ch_visible[1]:
            self._ch1_line.setData(t, v)
        elif ch == 2 and self._ch_visible[2]:
            self._ch2_line.setData(t, v)

        # Count one "frame" per Ch1 update, not per channel - otherwise dual-
        # channel mode double-counts and the FPS readout doubles, which
        # misrepresents the actual screen refresh rate.
        if ch == 1:
            self._frames += 1
        if self._single_pending and ch == 1:
            self._single_pending = False
            self._set_state(False)

    def _tick(self):
        # Drain Backend's data queue -> invokes _on_backend_data on this thread
        self.bridge.pump()
        now = time.time()
        if now - self._last_time >= 0.5:
            self.fps_lbl.setText(
                f"FPS: {self._frames / (now - self._last_time):.1f}")
            # Run the FFT measurement here (2 Hz), not per block, from the latest
            # raw block stashed by _on_backend_data.
            for ch in (1, 2):
                raw = self._last_raw.get(ch)
                if raw is not None:
                    self._meas[ch] = self._measure(raw[0], raw[1])
            self._update_meas_label()
            self._frames = 0
            self._last_time = now

    def closeEvent(self, event):
        self.bridge.stop()
        event.accept()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", help="USB serial port to open (else MockMCU)")
    args, _ = p.parse_known_args()

    app = QApplication(sys.argv)
    win = OscilloscopeUI(port=args.port)
    win.show()
    sys.exit(app.exec_())
