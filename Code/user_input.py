"""User Input block for the FastOscilloscope.

Standalone tkinter GUI that emits structured command dicts via a registered
callback. No hardware, USB, or threading concerns live here.
"""

import sys
import traceback
import tkinter as tk
from tkinter import ttk


def _eng_format_seconds(s: float) -> str:
    """Format seconds in 1-2-5 engineering style: 1us, 500ms, 1s, etc."""
    if s >= 1.0:
        return f"{s:g} s/div"
    if s >= 1e-3:
        return f"{s * 1e3:g} ms/div"
    if s >= 1e-6:
        return f"{s * 1e6:g} us/div"
    return f"{s * 1e9:g} ns/div"


def _eng_format_volts(v: float) -> str:
    if v >= 1.0:
        return f"{v:g} V/div"
    return f"{v * 1e3:g} mV/div"


def _one_two_five_sequence(low: float, high: float):
    """Yield 1-2-5 decade sequence between low and high inclusive."""
    values = []
    decade = low
    while decade <= high * 1.0001:
        for m in (1, 2, 5):
            v = decade * m
            if low * 0.9999 <= v <= high * 1.0001:
                values.append(v)
        decade *= 10
    return values


class UserInputBlock:
    """Oscilloscope front-panel control surface.

    Build the window, then wire a backend in via register_callback().
    """

    TRIGGER_LIMIT = 15.0  # Volts

    def __init__(self, verbose=False):
        self._user_callback = None
        self._verbose = verbose
        self._running = True  # tracked so spacebar can toggle Run/Stop

        self.root = tk.Tk()
        self.root.title("Oscilloscope - User Input")
        self.root.resizable(False, False)

        # Style for invalid-entry feedback
        self._style = ttk.Style()
        self._style.configure("Invalid.TEntry", fieldbackground="#ffd6d6")
        self._style.configure("Default.TEntry", fieldbackground="white")

        # Build precomputed value lists
        self._timebase_values = _one_two_five_sequence(1e-6, 1.0)
        self._vdiv_values = _one_two_five_sequence(0.01, 10.0)

        # Map display label -> float value, for combobox lookups
        self._timebase_lookup = {_eng_format_seconds(v): v for v in self._timebase_values}
        self._vdiv_lookup = {_eng_format_volts(v): v for v in self._vdiv_values}

        self._build_horizontal()
        self._build_vertical()
        self._build_channels()
        self._build_trigger()
        self._build_acquisition()

        # Keyboard shortcut: spacebar toggles Run/Stop
        self.root.bind("<space>", self._on_space)

    # ---------- callback plumbing ----------

    def register_callback(self, func):
        """Register a function to receive every command dict emitted."""
        self._user_callback = func

    def _callback(self, cmd: dict):
        print(f"[CMD] {cmd}")
        if self._user_callback is None:
            return
        try:
            self._user_callback(cmd)
        except Exception:
            print("[CMD] callback raised:")
            traceback.print_exc()

    def _log_event(self, description: str, raw_event=None):
        """Log a user-input event at the OS layer (separate from [CMD] output)."""
        print(f"[EVENT] {description}")
        if self._verbose and raw_event is not None:
            attrs = {
                k: getattr(raw_event, k, None)
                for k in ("widget", "type", "x", "y", "keysym", "num")
            }
            print(f"        raw: {attrs}")

    # ---------- A) Horizontal (timebase) ----------

    def _build_horizontal(self):
        frame = ttk.LabelFrame(self.root, text="Horizontal (Timebase)", padding=8)
        frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(frame, text="Time/div:").grid(row=0, column=0, sticky="w")
        self._tb_combo = ttk.Combobox(
            frame,
            state="readonly",
            values=list(self._timebase_lookup.keys()),
            width=14,
        )
        # Default to 500 us/div if present, else first
        default_tb = "500 us/div" if "500 us/div" in self._timebase_lookup else next(iter(self._timebase_lookup))
        self._tb_combo.set(default_tb)
        self._tb_combo.grid(row=0, column=1, padx=6)
        self._tb_combo.bind("<<ComboboxSelected>>", self._on_timebase)

    def _on_timebase(self, event=None):
        label = self._tb_combo.get()
        value = self._timebase_lookup[label]
        self._log_event(f"Combobox change on 'Timebase' -> {label}", event)
        self._callback({"cmd": "set_timebase", "value": float(value)})

    # ---------- B) Vertical (V/div + offset) ----------

    def _build_vertical(self):
        frame = ttk.LabelFrame(self.root, text="Vertical", padding=8)
        frame.pack(fill="x", padx=8, pady=4)

        # Volts/div
        ttk.Label(frame, text="V/div:").grid(row=0, column=0, sticky="w")
        self._vdiv_combo = ttk.Combobox(
            frame,
            state="readonly",
            values=list(self._vdiv_lookup.keys()),
            width=14,
        )
        default_vdiv = "1 V/div" if "1 V/div" in self._vdiv_lookup else next(iter(self._vdiv_lookup))
        self._vdiv_combo.set(default_vdiv)
        self._vdiv_combo.grid(row=0, column=1, padx=6)
        self._vdiv_combo.bind("<<ComboboxSelected>>", self._on_vdiv)

        # Offset
        ttk.Label(frame, text="Offset:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._offset_var = tk.DoubleVar(value=0.0)
        self._offset_scale = ttk.Scale(
            frame,
            from_=-5.0,
            to=5.0,
            orient="horizontal",
            length=200,
            variable=self._offset_var,
            command=self._on_offset,
        )
        self._offset_scale.grid(row=1, column=1, padx=6, pady=(6, 0))
        self._offset_label = ttk.Label(frame, text="0.00 V")
        self._offset_label.grid(row=1, column=2, padx=6, pady=(6, 0))

    def _on_vdiv(self, event=None):
        label = self._vdiv_combo.get()
        value = self._vdiv_lookup[label]
        self._log_event(f"Combobox change on 'V/div' -> {label}", event)
        self._callback({"cmd": "set_vdiv", "value": float(value)})

    def _on_offset(self, raw):
        # ttk.Scale calls back with a string
        v = round(float(raw), 2)
        self._offset_label.configure(text=f"{v:+.2f} V")
        self._log_event(f"Slider change on 'Offset' -> {v:+.2f} V")
        self._callback({"cmd": "set_voffset", "value": v})

    # ---------- B2) Channels ----------

    def _build_channels(self):
        frame = ttk.LabelFrame(self.root, text="Channels", padding=8)
        frame.pack(fill="x", padx=8, pady=4)

        self._ch_vars = {}
        for i in (1, 2):
            var = tk.BooleanVar(value=True)
            self._ch_vars[i] = var
            ttk.Checkbutton(
                frame,
                text=f"Ch{i}",
                variable=var,
                command=lambda ch=i: self._on_channel_toggle(ch),
            ).pack(side="left", padx=8)

    def _on_channel_toggle(self, channel: int):
        enabled = bool(self._ch_vars[channel].get())
        self._log_event(f"Checkbox toggle on 'Ch{channel}' -> {enabled}")
        self._callback({"cmd": "set_channel", "channel": int(channel), "enabled": enabled})
        self._refresh_trigger_sources()

    def _refresh_trigger_sources(self):
        """Trigger source list reflects currently-enabled channels."""
        enabled = [f"Ch{i}" for i in (1, 2) if self._ch_vars[i].get()]
        if not enabled:
            enabled = ["Ch1"]  # always offer at least one
        self._trig_source["values"] = enabled
        if self._trig_source.get() not in enabled:
            self._trig_source.set(enabled[0])

    # ---------- C) Trigger ----------

    def _build_trigger(self):
        frame = ttk.LabelFrame(self.root, text="Trigger", padding=8)
        frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(frame, text="Level (V):").grid(row=0, column=0, sticky="w")

        vcmd = (self.root.register(self._validate_float), "%P")
        self._trig_entry = ttk.Entry(
            frame, width=10, validate="key", validatecommand=vcmd, style="Default.TEntry"
        )
        self._trig_entry.insert(0, "0.0")
        self._trig_entry.grid(row=0, column=1, padx=6)
        self._trig_entry.bind("<Return>", lambda _e: self._commit_trigger())

        ttk.Label(frame, text="Mode:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self._trig_mode = ttk.Combobox(
            frame, state="readonly", values=["rising", "falling", "auto"], width=10
        )
        self._trig_mode.set("rising")
        self._trig_mode.grid(row=0, column=3, padx=6)
        # Mode changes auto-emit a set_trigger command
        self._trig_mode.bind(
            "<<ComboboxSelected>>",
            lambda e: (self._log_event(f"Combobox change on 'Trigger Mode' -> {self._trig_mode.get()}", e),
                       self._commit_trigger()),
        )

        ttk.Label(frame, text="Source:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._trig_source = ttk.Combobox(
            frame, state="readonly", values=["Ch1", "Ch2"], width=10
        )
        self._trig_source.set("Ch1")
        self._trig_source.grid(row=1, column=1, padx=6, pady=(6, 0))
        self._trig_source.bind(
            "<<ComboboxSelected>>",
            lambda e: (self._log_event(f"Combobox change on 'Trigger Source' -> {self._trig_source.get()}", e),
                       self._commit_trigger()),
        )

        ttk.Button(frame, text="Apply", command=self._on_apply_trigger).grid(
            row=0, column=4, padx=6
        )

    def _on_apply_trigger(self):
        self._log_event("Button click on 'Apply Trigger'")
        self._commit_trigger()

    @staticmethod
    def _validate_float(proposed: str) -> bool:
        # Allow empty / partial entries while typing
        if proposed in ("", "-", "+", ".", "-.", "+."):
            return True
        try:
            float(proposed)
            return True
        except ValueError:
            return False

    def _commit_trigger(self):
        raw = self._trig_entry.get()
        try:
            level = float(raw)
        except ValueError:
            self._trig_entry.configure(style="Invalid.TEntry")
            return

        clamped = max(-self.TRIGGER_LIMIT, min(self.TRIGGER_LIMIT, level))
        if clamped != level:
            # Reflect the clamped value back to the user
            self._trig_entry.delete(0, tk.END)
            self._trig_entry.insert(0, f"{clamped:g}")

        self._trig_entry.configure(style="Default.TEntry")
        self._callback({
            "cmd": "set_trigger",
            "level": float(clamped),
            "mode": self._trig_mode.get(),
            "source": self._trig_source.get(),
        })

    # ---------- D) Acquisition ----------

    def _build_acquisition(self):
        frame = ttk.LabelFrame(self.root, text="Acquisition", padding=8)
        frame.pack(fill="x", padx=8, pady=4)

        ttk.Button(frame, text="Run", width=10, command=self._on_run).pack(side="left", padx=4)
        ttk.Button(frame, text="Stop", width=10, command=self._on_stop).pack(side="left", padx=4)
        ttk.Button(frame, text="Single", width=10, command=self._on_single).pack(side="left", padx=4)

    def _on_run(self):
        self._log_event("Button click on 'Run'")
        self._running = True
        self._callback({"cmd": "run"})

    def _on_stop(self):
        self._log_event("Button click on 'Stop'")
        self._running = False
        self._callback({"cmd": "stop"})

    def _on_single(self):
        self._log_event("Button click on 'Single'")
        self._callback({"cmd": "single"})

    def _on_space(self, event=None):
        """Spacebar shortcut: toggle Run <-> Stop."""
        self._log_event("Key '<space>' (Run/Stop toggle)", event)
        if self._running:
            self._running = False
            self._callback({"cmd": "stop"})
        else:
            self._running = True
            self._callback({"cmd": "run"})

    # ---------- run ----------

    def mainloop(self):
        self.root.mainloop()


if __name__ == "__main__":
    def mock_backend(cmd):
        print(f"[BACKEND] received: {cmd}")

    verbose = "--verbose" in sys.argv
    ui = UserInputBlock(verbose=verbose)
    ui.register_callback(mock_backend)
    ui.mainloop()
