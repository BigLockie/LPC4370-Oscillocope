"""Block 1 - Test 5: command rate + latency.

Drives 100 GUI control changes per second for 5 seconds (500 total) by
cycling the timebase combobox, then reports loss count and mean callback
latency. Pass: 0 lost, mean latency < 10 ms.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from user_input import UserInputBlock

TOTAL_COMMANDS = 500
INTERVAL_MS = 10  # 10ms -> 100/sec


def main():
    received = []
    pending = {"dispatch": 0.0}

    def counter(_cmd):
        # Latency = time from dispatch (just before invoke) to callback entry.
        received.append(time.perf_counter() - pending["dispatch"])

    ui = UserInputBlock()
    ui.register_callback(counter)

    # Cycle the timebase combobox through all available values.
    timebase_labels = list(ui._timebase_lookup.keys())

    state = {"generated": 0, "start": 0.0}

    def fire():
        if state["generated"] == 0:
            state["start"] = time.perf_counter()

        if state["generated"] >= TOTAL_COMMANDS:
            elapsed = time.perf_counter() - state["start"]
            generated = state["generated"]
            recv = len(received)
            loss = generated - recv
            mean_latency_ms = (sum(received) / len(received) * 1000) if received else float("inf")
            print(f"[RESULT] elapsed={elapsed:.2f}s generated={generated} "
                  f"received={recv} loss={loss} mean_latency={mean_latency_ms:.3f} ms")
            ok = loss == 0 and mean_latency_ms < 10.0
            print("[RESULT] PASS" if ok else "[RESULT] FAIL")
            ui.root.quit()
            return

        label = timebase_labels[state["generated"] % len(timebase_labels)]
        ui._tb_combo.set(label)
        pending["dispatch"] = time.perf_counter()
        # Fire the same handler the <<ComboboxSelected>> event would.
        ui._on_timebase()
        state["generated"] += 1
        ui.root.after(INTERVAL_MS, fire)

    ui.root.after(100, fire)
    ui.mainloop()


if __name__ == "__main__":
    main()
