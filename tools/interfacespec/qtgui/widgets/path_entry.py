"""
LabeledPathEntry — a Label + Combobox (with recent history) + Browse button composite widget.
"""

import tkinter as tk
from tkinter import filedialog, ttk
from typing import List

from ..font_manager import FontManager


def _prepend_recent(value: str, existing: List[str], max_items: int = 3) -> List[str]:
    """Return a new list with *value* at the front, deduplicated, capped at max_items."""
    value = value.strip()
    if not value:
        return existing[:max_items]
    deduped = [value] + [p for p in existing if p != value]
    return deduped[:max_items]


class LabeledPathEntry(ttk.Frame):
    """
    A labeled, editable combobox with Browse button and a recent-paths dropdown.

    Usage:
        w = LabeledPathEntry(parent, label="Model root:", mode="dir")
        w.get()                    # returns current path string
        w.set("/some/path")
        w.set_recent(["/a", "/b"]) # populate dropdown history
        w.get_recent()             # returns updated recent list (current prepended)
        w.trace(callback)          # called whenever value changes
    """

    def __init__(self, parent, label: str = "", mode: str = "dir",
                 file_types=None, width: int = 55,
                 placeholder: str = "", **kwargs):
        """
        Args:
            label:       Label text shown to the left.
            mode:        "dir" for directory chooser, "file" for file chooser.
            file_types:  list of (desc, pattern) for file chooser.
            width:       character width of the entry widget.
            placeholder: grey hint text shown when the entry is empty.
        """
        super().__init__(parent, **kwargs)
        self._mode = mode
        self._file_types = file_types or [("All files", "*.*")]
        self._placeholder = placeholder
        self._var = tk.StringVar()
        self._recent: List[str] = []

        self._label = ttk.Label(self, text=label, width=16, anchor="w",
                                font=FontManager.get("normal"))
        self._label.pack(side=tk.LEFT)

        # Combobox: editable (state="normal") so the user can still type freely;
        # the dropdown list shows recent paths.
        self._combo = ttk.Combobox(self, textvariable=self._var, width=width,
                                   font=FontManager.get("normal"),
                                   state="normal")
        self._combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        # Placeholder implementation
        if placeholder:
            self._show_placeholder()
            self._combo.bind("<FocusIn>",  self._on_focus_in)
            self._combo.bind("<FocusOut>", self._on_focus_out)
            self._var.trace_add("write", self._on_var_change)

        self._browse_btn = ttk.Button(self, text="Browse…", command=self._browse)
        self._browse_btn.pack(side=tk.LEFT)

        FontManager.add_listener(self._on_font_change)

    # ------------------------------------------------------------------
    # Placeholder helpers
    # ------------------------------------------------------------------
    def _show_placeholder(self):
        self._combo.configure(foreground="grey")
        if not self._var.get():
            self._var.set(self._placeholder)

    def _hide_placeholder(self):
        if self._var.get() == self._placeholder:
            self._var.set("")
        self._combo.configure(foreground="")

    def _on_focus_in(self, _event=None):
        if self._var.get() == self._placeholder:
            self._hide_placeholder()

    def _on_focus_out(self, _event=None):
        if not self._var.get().strip():
            self._show_placeholder()

    def _on_var_change(self, *_):
        if self._var.get() == self._placeholder:
            self._combo.configure(foreground="grey")
        else:
            self._combo.configure(foreground="")

    def _on_font_change(self):
        f = FontManager.get("normal")
        self._label.configure(font=f)
        self._combo.configure(font=f)

    def _browse(self):
        if self._mode == "dir":
            path = filedialog.askdirectory(initialdir=self.get() or "/")
        else:
            path = filedialog.askopenfilename(
                initialdir=self.get() or "/",
                filetypes=self._file_types,
            )
        if path:
            self._var.set(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get(self) -> str:
        v = self._var.get().strip()
        return "" if (self._placeholder and v == self._placeholder) else v

    def set(self, value: str):
        self._var.set(value)
        if self._placeholder and not value.strip():
            self._show_placeholder()

    def set_recent(self, paths: List[str]):
        """Populate the dropdown with recently-used paths (up to 3)."""
        self._recent = list(paths)[:3]
        self._combo["values"] = self._recent

    def get_recent(self) -> List[str]:
        """Return an updated recent list with the current value prepended."""
        updated = _prepend_recent(self.get(), self._recent)
        self._recent = updated
        self._combo["values"] = self._recent
        return self._recent

    @property
    def var(self) -> tk.StringVar:
        """Expose the underlying StringVar for external traces."""
        return self._var

    def trace(self, callback):
        """Register a callback invoked whenever the path value changes."""
        self._var.trace_add("write", lambda *_: callback(self._var.get()))
