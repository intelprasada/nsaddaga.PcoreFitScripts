"""tk_widgets.py – Shared Tkinter widgets for core-tools GUI applications.

These widgets are tool-agnostic and can be imported by any tool that adds
``lib/python`` to its ``PYTHONPATH`` (done automatically by the ``bin/``
entry-point wrappers).
"""

import tkinter as tk
from tkinter import ttk


class TabTooltip:
    """Tooltip that shows a string when hovering over a ttk.Notebook tab.

    Attach once to a Notebook; tooltip text is read from the ``_path``
    attribute stored on each tab's child widget (set it to whatever string
    you want displayed).  If ``_path`` is absent or empty no tooltip appears.

    Usage::

        TabTooltip(notebook)                               # default font
        TabTooltip(notebook, font=FontManager.get("small"))  # custom font
    """

    _DELAY_MS = 600  # ms before the tooltip becomes visible

    def __init__(self, notebook: ttk.Notebook, font=None):
        self._nb       = notebook
        self._font     = font
        self._win:      "tk.Toplevel | None" = None
        self._after_id: "str | None"         = None
        self._last_tab: "int | None"         = None

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
            # Mouse is over the content area, not the tab strip.
            self._hide()
            self._last_tab = None
            return

        tab_id = int(raw)

        if tab_id != self._last_tab:
            # Entered a different tab — reset and schedule a fresh tooltip.
            self._last_tab = tab_id
            self._hide()
            self._after_id = self._nb.after(
                self._DELAY_MS,
                lambda tid=tab_id, rx=event.x_root, ry=event.y_root:
                    self._show(tid, rx, ry),
            )
        elif self._win:
            # Same tab — keep the tooltip near the cursor, clamped to screen.
            tw = self._win.winfo_reqwidth()
            th = self._win.winfo_reqheight()
            sw = self._win.winfo_screenwidth()
            sh = self._win.winfo_screenheight()

            x = event.x_root + 14
            y = event.y_root + 20
            if x + tw > sw:
                x = sw - tw
            if y + th > sh:
                y = event.y_root - th - 4
            x = max(0, x)
            y = max(0, y)

            self._win.geometry(f"+{x}+{y}")

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

        self._hide()  # destroy any stale window

        win = tk.Toplevel(self._nb)
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        # Neutral tooltip colours that read well on any theme.
        label_kw: dict = dict(
            text=text,
            background="#ffffe0",
            foreground="#1a1a1a",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=4,
        )
        if self._font is not None:
            label_kw["font"] = self._font
        tk.Label(win, **label_kw).pack()

        # Place off-screen first so Tk can compute the real size, then
        # clamp so the tooltip never spills outside the screen.
        win.geometry("+0+0")
        win.update_idletasks()

        tw = win.winfo_reqwidth()
        th = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()

        # Preferred position: just below and to the right of the cursor.
        x = root_x + 14
        y = root_y + 20

        # Right edge spills → shift left so tooltip fits within screen.
        if x + tw > sw:
            x = sw - tw

        # Bottom edge spills → show tooltip above the cursor instead.
        if y + th > sh:
            y = root_y - th - 4

        # Never allow negative coordinates.
        x = max(0, x)
        y = max(0, y)

        win.geometry(f"+{x}+{y}")
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
