"""
LogPanel — a scrollable, color-coded log output widget.
"""

import tkinter as tk
from tkinter import ttk

from ..font_manager import FontManager


# Tag name → foreground color (vivid, readable on dark background)
_TAG_COLORS = {
    "INFO":  "#e8e8e8",
    "STEP":  "#4fc3f7",   # bright sky blue
    "OK":    "#69f0ae",   # vivid green
    "WARN":  "#ffd740",   # amber yellow
    "ERROR": "#ff5252",   # vivid red
    "DIM":   "#b0b0b0",   # light grey
}

_LOG_BG = "#1a1a2e"  # deep navy


class LogPanel(ttk.Frame):
    """
    Scrollable Text widget for streaming log output.

    Usage:
        panel = LogPanel(parent)
        panel.append("Running step 01...", level="STEP")
        panel.append("Error: file not found", level="ERROR")
        panel.clear()
    """

    def __init__(self, parent, height: int = 14, **kwargs):
        super().__init__(parent, **kwargs)

        # Toolbar row
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X)
        self._title_lbl = ttk.Label(toolbar, text="Log", font=FontManager.get("bold"))
        self._title_lbl.pack(side=tk.LEFT, padx=4)
        self._clear_btn = ttk.Button(toolbar, text="Clear", command=self.clear, width=6)
        self._clear_btn.pack(side=tk.RIGHT, padx=4)

        # Text area + scrollbar
        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            frame,
            height=height,
            bg=_LOG_BG,
            fg=_TAG_COLORS["INFO"],
            font=FontManager.get("mono"),
            state=tk.DISABLED,
            wrap=tk.WORD,
            relief=tk.FLAT,
            bd=0,
            selectbackground="#3a3a5c",
            selectforeground="#ffffff",
        )
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Configure color tags
        for tag, color in _TAG_COLORS.items():
            self._text.tag_configure(tag, foreground=color)

        # Respond to font size changes
        FontManager.add_listener(self._on_font_change)

    def _on_font_change(self):
        self._title_lbl.configure(font=FontManager.get("bold"))
        self._text.configure(font=FontManager.get("mono"))

    def append(self, line: str, level: str = "INFO"):
        """Append a line to the log with the given severity level tag."""
        level = level.upper()
        if level not in _TAG_COLORS:
            level = "INFO"
        self._text.configure(state=tk.NORMAL)
        self._text.insert(tk.END, line.rstrip("\n") + "\n", level)
        self._text.configure(state=tk.DISABLED)
        self._text.see(tk.END)

    def clear(self):
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.configure(state=tk.DISABLED)

    def auto_level(self, line: str) -> str:
        """Heuristically pick a log level from the line content."""
        ll = line.lower()
        if "error" in ll or "traceback" in ll or "exception" in ll:
            return "ERROR"
        if "warn" in ll:
            return "WARN"
        if "wrote" in ll or "done" in ll or "complete" in ll or "exit: 0" in ll:
            return "OK"
        if line.strip().startswith("[") or "step" in ll:
            return "STEP"
        return "INFO"
