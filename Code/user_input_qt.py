"""PyQt5 sibling of UserInputBlock.

Same callback API and command-dict schema as Code/user_input.py (tkinter
version used for Block 1 verification), but built with Qt widgets so it can
share the PyQt event loop in main.py.
"""

import traceback

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QDoubleValidator, QKeySequence
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QShortcut, QSlider, QVBoxLayout, QWidget,
)


def _eng_seconds(s: float) -> str:
    if s >= 1.0:
        return f"{s:g} s/div"
    if s >= 1e-3:
        return f"{s * 1e3:g} ms/div"
    if s >= 1e-6:
        return f"{s * 1e6:g} us/div"
    return f"{s * 1e9:g} ns/div"


def _eng_volts(v: float) -> str:
    if v >= 1.0:
        return f"{v:g} V/div"
    return f"{v * 1e3:g} mV/div"


def _one_two_five(low: float, high: float):
    out, decade = [], low
    while decade <= high * 1.0001:
        for m in (1, 2, 5):
            v = decade * m
            if low * 0.9999 <= v <= high * 1.0001:
                out.append(v)
        decade *= 10
    return out


class UserInputPanelQt(QWidget):
    """Embeddable Qt control panel that emits oscilloscope command dicts."""

    TRIGGER_LIMIT = 15.0

    def __init__(self, parent=None, verbose=False):
        super().__init__(parent)
        self._user_callback = None
        self._verbose = verbose
        self._running = True

        self._timebase_values = _one_two_five(1e-6, 1.0)
        self._vdiv_values = _one_two_five(0.01, 10.0)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        root.addWidget(self._build_horizontal())
        root.addWidget(self._build_vertical())
        root.addWidget(self._build_channels())
        root.addWidget(self._build_trigger())
        root.addWidget(self._build_acquisition())
        root.addWidget(self._build_mock_signal())
        root.addStretch(1)

        sc = QShortcut(QKeySequence(Qt.Key_Space), self)
        sc.activated.connect(self._on_space)

    # ---------- callback plumbing ----------

    def register_callback(self, func):
        self._user_callback = func

    def _emit(self, cmd: dict):
        print(f"[CMD] {cmd}")
        if self._user_callback is None:
            return
        try:
            self._user_callback(cmd)
        except Exception:
            print("[CMD] callback raised:")
            traceback.print_exc()

    def _log_event(self, description: str):
        if self._verbose:
            print(f"[EVENT] {description}")

    # ---------- Horizontal ----------

    def _build_horizontal(self):
        gb = QGroupBox("Horizontal")
        form = QFormLayout(gb)
        self._tb = QComboBox()
        for v in self._timebase_values:
            self._tb.addItem(_eng_seconds(v), v)
        idx = self._tb.findData(5e-5)
        self._tb.setCurrentIndex(idx if idx >= 0 else 0)
        self._tb.currentIndexChanged.connect(self._on_timebase)
        form.addRow("Time/div:", self._tb)
        return gb

    def _on_timebase(self, _i):
        v = float(self._tb.currentData())
        self._log_event(f"Timebase -> {self._tb.currentText()}")
        self._emit({"cmd": "set_timebase", "value": v})

    # ---------- Vertical ----------

    def _build_vertical(self):
        gb = QGroupBox("Vertical")
        form = QFormLayout(gb)

        self._vdiv = QComboBox()
        for v in self._vdiv_values:
            self._vdiv.addItem(_eng_volts(v), v)
        idx = self._vdiv.findData(3.0)
        self._vdiv.setCurrentIndex(idx if idx >= 0 else 0)
        self._vdiv.currentIndexChanged.connect(self._on_vdiv)
        form.addRow("V/div:", self._vdiv)

        self._offset = QSlider(Qt.Horizontal)
        self._offset.setRange(-500, 500)
        self._offset.setValue(0)
        self._offset.valueChanged.connect(self._on_offset)
        self._offset_lbl = QLabel("+0.00 V")
        self._offset_lbl.setMinimumWidth(60)
        row = QHBoxLayout()
        row.addWidget(self._offset, 1)
        row.addWidget(self._offset_lbl)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("Offset:", wrap)

        return gb

    def _on_vdiv(self, _i):
        v = float(self._vdiv.currentData())
        self._log_event(f"V/div -> {self._vdiv.currentText()}")
        self._emit({"cmd": "set_vdiv", "value": v})

    def _on_offset(self, raw):
        v = round(raw / 100.0, 2)
        self._offset_lbl.setText(f"{v:+.2f} V")
        self._emit({"cmd": "set_voffset", "value": v})

    # ---------- Channels ----------

    def _build_channels(self):
        gb = QGroupBox("Channels")
        row = QHBoxLayout(gb)
        self._ch1 = QCheckBox("Ch1")
        self._ch2 = QCheckBox("Ch2")
        self._ch1.setChecked(True)
        self._ch2.setChecked(True)
        self._ch1.toggled.connect(lambda on: self._on_channel(1, on))
        self._ch2.toggled.connect(lambda on: self._on_channel(2, on))
        row.addWidget(self._ch1)
        row.addWidget(self._ch2)
        row.addStretch(1)
        return gb

    def _on_channel(self, ch: int, enabled: bool):
        self._log_event(f"Ch{ch} -> {enabled}")
        self._emit({"cmd": "set_channel", "channel": int(ch), "enabled": bool(enabled)})
        sources = []
        if self._ch1.isChecked(): sources.append("Ch1")
        if self._ch2.isChecked(): sources.append("Ch2")
        if not sources: sources = ["Ch1"]
        current = self._trig_source.currentText()
        self._trig_source.blockSignals(True)
        self._trig_source.clear()
        self._trig_source.addItems(sources)
        self._trig_source.setCurrentText(current if current in sources else sources[0])
        self._trig_source.blockSignals(False)

    # ---------- Trigger ----------

    def _build_trigger(self):
        gb = QGroupBox("Trigger")
        form = QFormLayout(gb)

        self._trig_level = QLineEdit("0.0")
        self._trig_level.setValidator(QDoubleValidator(-self.TRIGGER_LIMIT, self.TRIGGER_LIMIT, 4, self))
        self._trig_level.returnPressed.connect(self._commit_trigger)
        form.addRow("Level (V):", self._trig_level)

        self._trig_mode = QComboBox()
        self._trig_mode.addItems(["rising", "falling", "auto"])
        self._trig_mode.currentIndexChanged.connect(lambda _i: self._commit_trigger())
        form.addRow("Mode:", self._trig_mode)

        self._trig_source = QComboBox()
        self._trig_source.addItems(["Ch1", "Ch2"])
        self._trig_source.currentIndexChanged.connect(lambda _i: self._commit_trigger())
        form.addRow("Source:", self._trig_source)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._commit_trigger)
        form.addRow("", apply_btn)

        return gb

    def _commit_trigger(self):
        raw = self._trig_level.text().strip()
        try:
            level = float(raw)
        except ValueError:
            self._trig_level.setStyleSheet("background-color: #ffd6d6; color: black;")
            return
        clamped = max(-self.TRIGGER_LIMIT, min(self.TRIGGER_LIMIT, level))
        if clamped != level:
            self._trig_level.setText(f"{clamped:g}")
        self._trig_level.setStyleSheet("")
        self._emit({
            "cmd": "set_trigger",
            "level": float(clamped),
            "mode": self._trig_mode.currentText(),
            "source": self._trig_source.currentText(),
        })

    # ---------- Acquisition ----------

    def _build_acquisition(self):
        gb = QGroupBox("Acquisition")
        row = QHBoxLayout(gb)
        for label, slot in (("Run", self._on_run), ("Stop", self._on_stop), ("Single", self._on_single)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        return gb

    def _on_run(self):
        self._log_event("Run clicked")
        self._running = True
        self._emit({"cmd": "run"})

    def _on_stop(self):
        self._log_event("Stop clicked")
        self._running = False
        self._emit({"cmd": "stop"})

    def _on_single(self):
        self._log_event("Single clicked")
        self._emit({"cmd": "single"})

    def _on_space(self):
        self._log_event("Spacebar Run/Stop toggle")
        if self._running:
            self._running = False
            self._emit({"cmd": "stop"})
        else:
            self._running = True
            self._emit({"cmd": "run"})

    # ---------- Mock Signal Generator ----------

    def _build_mock_signal(self):
        gb = QGroupBox("Mock Signal Generator")
        form = QFormLayout(gb)
        self._ch1_f = QDoubleSpinBox(); self._ch1_f.setRange(0, 10000); self._ch1_f.setValue(5.0);  self._ch1_f.setSuffix(" kHz")
        self._ch2_f = QDoubleSpinBox(); self._ch2_f.setRange(0, 10000); self._ch2_f.setValue(15.0); self._ch2_f.setSuffix(" kHz")
        self._ch1_a = QDoubleSpinBox(); self._ch1_a.setRange(0, 15.0);  self._ch1_a.setValue(5.0);  self._ch1_a.setSuffix(" V")
        self._ch2_a = QDoubleSpinBox(); self._ch2_a.setRange(0, 15.0);  self._ch2_a.setValue(5.0);  self._ch2_a.setSuffix(" V")
        for w in (self._ch1_f, self._ch2_f, self._ch1_a, self._ch2_a):
            w.valueChanged.connect(self._on_mock_signal)
        form.addRow("Ch1 freq:", self._ch1_f)
        form.addRow("Ch2 freq:", self._ch2_f)
        form.addRow("Ch1 amp:",  self._ch1_a)
        form.addRow("Ch2 amp:",  self._ch2_a)
        return gb

    def _on_mock_signal(self):
        self._emit({
            "cmd": "set_mock_signal",
            "ch1_freq_khz": self._ch1_f.value(),
            "ch2_freq_khz": self._ch2_f.value(),
            "ch1_amp_v":    self._ch1_a.value(),
            "ch2_amp_v":    self._ch2_a.value(),
        })
