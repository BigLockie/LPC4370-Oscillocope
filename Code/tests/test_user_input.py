"""Block 1 verification harness.

Launches the User Input GUI with a mock backend that logs every received
command. Pass --verbose to also dump raw tkinter Event attributes for each
input event.

Used by Tests 1, 3, 4, 6.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from user_input import UserInputBlock


def mock_backend(cmd):
    """Mock Backend block: just logs commands as it receives them."""
    print(f"[BACKEND] received: {cmd}")


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv
    ui = UserInputBlock(verbose=verbose)
    ui.register_callback(mock_backend)
    ui.mainloop()
