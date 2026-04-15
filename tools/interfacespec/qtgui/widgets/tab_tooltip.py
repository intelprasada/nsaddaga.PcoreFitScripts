"""
TabTooltip — hover tooltip for ttk.Notebook tabs.

Attach once to a Notebook; tooltip text is read from the ``_path``
attribute stored on each tab's child widget (set it to whatever string
you want displayed).  If ``_path`` is absent or empty no tooltip appears.

Usage::

    TabTooltip(notebook)   # no reference needs to be kept
"""

import tkinter as tk
from tkinter import ttk

from ..font_manager import FontManager


class TabTooltip:
    """Tooltip that shows a path string when hovering over a notebook tab.

    Appears after a short delay, follows the cursor within the same tab,
    and disappears when the cursor leaves the tab strip.
    """

    _DELAY_MS = 600  # ms before the tooltip becomes visible

    def __init__(self, notebook: ttk.Notebook):
        self._nb = notebook
        self._win: "tk.Toplevel | None" = None
        self._after_id: "str | None" = None
        self._last_tab: "int | None" = None

        notebook.bind("<Motion>", self._on_motion, add="+")
        notebook.bind("<Leave>",  self._on_leave,  add="+")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_motion(self, event: tk.Event):
        try:
            raw = self._nb.tk.call(self._nb._w, "identify", "tab",
                                   event.x, event.y)
        except tk.TclError:
            self._hide()
            return

        if raw == "" or raw is None:
            self._hide()
            self._last_tab = None
            return

        tab_id = int(raw)

        if tab_id != self._last_tab:
            self._last_tab = tab_id
            self._hide()
            self._after_id = self._nb.after(
                self._DELAY_MS,
                lambda tid=tab_id, rx=event.x_root, ry=event.y_root:
                    self._show(tid, rx, ry),
            )
        elif self._win:
            self._win.geometry(f"+{event.x_root + 14}+{event.y_root + 20}")

    def _on_leave(self, _event):
        self._hide()
        self._last_tab = None

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def _show(self, tab_id: int, root_x: int, root_y: int):
        """Create the tooltip Toplevel for *tab_id*."""
        self._after_id = None
        try:
            widget = self._nb.nametowidget(self._nb.tabs()[tab_id])
        except (IndexError, tk.TclError):
            return

        text = getattr(widget, "_path", None)
        if not text:
            return

        self._hide()

        win = tk.Toplevel(self._nb)
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        tk.Label(
            win,
            text=text,
            background="#ffffe0",
            foreground="#1a1a1a",
            relief="solid",
            borderwidth=1,
            font=FontManager.get("small"),
            padx=8,
            pady=4,
        ).pack()

        win.geometry(f"+{root_x + 14}+{root_y + 20}")
        self._win = win

    def _hide(self):
        """Cancel any pending show and destroy the tooltip window."""
        if self._after_id is not None:
            try:
                self._nb.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
