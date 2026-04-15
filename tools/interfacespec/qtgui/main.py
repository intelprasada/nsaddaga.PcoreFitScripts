"""
Entry point for the InterfaceSpec Pipeline GUI tool.

Usage:
    python3 scripts/interfacespec/qtgui/main.py

Or from the model root:
    python3 -m scripts.interfacespec.qtgui.main
"""

import sys
import os
import tkinter as tk

# Allow running as a plain script (`python3 main.py`) as well as a module.
if __package__ is None or __package__ == "":
    # Invoked directly: insert the qtgui parent dir so relative imports work.
    _here = os.path.dirname(os.path.abspath(__file__))
    _pkg_root = os.path.dirname(_here)          # scripts/interfacespec/
    _repo_root = os.path.dirname(os.path.dirname(_pkg_root))  # model root
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from qtgui.app import MainApp  # type: ignore
else:
    from .app import MainApp


def main():
    root = tk.Tk()
    MainApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
