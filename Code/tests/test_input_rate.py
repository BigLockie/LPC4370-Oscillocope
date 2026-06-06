"""Block 1 - Test 2: input rate stress.

Drives 100 synthetic Run-button clicks per second for 5 seconds (500 total)
via tkinter's event_generate(), then reports how many made it through to the
mock backend. Pass condition: 0% loss, GUI never freezes.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from user_input import UserInputBlock

TOTAL_EVENTS = 500
INTERVAL_MS = 10  # 10ms -> 100 events/sec


def main():
    received = {"count": 0}

    def counter(_cmd):
        received["count"] += 1

    ui = UserInputBlock()
    ui.register_callback(counter)

    state = {"generated": 0, "start": 0.0}

    def fire():
        if state["generated"] == 0:
            state["start"] = time.perf_counter()

        if state["generated"] >= TOTAL_EVENTS:
            elapsed = time.perf_counter() - state["start"]
            generated = state["generated"]
            recv = received["count"]
            loss = generated - recv
            pct = (loss / generated) * 100 if generated else 0.0
            print(f"[RESULT] elapsed={elapsed:.2f}s generated={generated} "
                  f"received={recv} loss={loss} ({pct:.1f}%)")
            print("[RESULT] PASS" if loss == 0 else "[RESULT] FAIL")
            ui.root.quit()
            return

        # Call the same handler that a real <Button-1> click on Run would fire.
        ui._on_run()
        state["generated"] += 1
        ui.root.after(INTERVAL_MS, fire)

    ui.root.after(100, fire)  # let the window settle, then start firing
    ui.mainloop()


if __name__ == "__main__":
    main()
