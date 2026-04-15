"""
FontManager — application-wide font size control.

All widgets that want to respond to font size changes should pass
``FontManager.get(role)`` as their ``font=`` argument.  Because ``get()``
returns a live ``tkfont.Font`` object (not a static tuple), reconfiguring
the object automatically updates every widget that holds a reference — no
``_on_font_change`` listeners are required for font updates.

Roles: "normal", "mono", "bold", "small", "heading"
"""

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, List

_DEFAULT_SIZE = 14
_MIN_SIZE = 7
_MAX_SIZE = 22


class FontManager:
    """Singleton managing application-wide font sizes."""

    _size: int = _DEFAULT_SIZE
    _listeners: List[Callable[[], None]] = []

    # Live Font objects — created lazily on first get() call (after Tk root).
    # Because every widget holds a reference to the same object, calling
    # .configure(size=…) on it updates all widgets simultaneously.
    _fonts: dict = {}

    # ------------------------------------------------------------------
    # Internal: lazy font initialisation
    # ------------------------------------------------------------------

    @classmethod
    def _init_fonts(cls):
        """Create Font objects the first time get() is called."""
        if cls._fonts:
            return
        s = cls._size
        cls._fonts = {
            "normal":  tkfont.Font(family="TkDefaultFont", size=s),
            "mono":    tkfont.Font(family="Courier",       size=s),
            "bold":    tkfont.Font(family="TkDefaultFont", size=s, weight="bold"),
            "small":   tkfont.Font(family="TkDefaultFont", size=max(s - 1, _MIN_SIZE)),
            "heading": tkfont.Font(family="TkDefaultFont", size=s + 3, weight="bold"),
        }

    @classmethod
    def _reconfigure_fonts(cls):
        """Push the current size into all Font objects."""
        if not cls._fonts:
            return
        s = cls._size
        cls._fonts["normal"].configure( size=s)
        cls._fonts["mono"].configure(   size=s)
        cls._fonts["bold"].configure(   size=s)
        cls._fonts["small"].configure(  size=max(s - 1, _MIN_SIZE))
        cls._fonts["heading"].configure(size=s + 3)

    # ------------------------------------------------------------------
    # Font accessors
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, role: str = "normal") -> tkfont.Font:
        """Return the live Font object for the given role.

        Passing the returned object as ``font=FontManager.get(role)`` to a
        widget means the widget auto-updates whenever the font size changes —
        no additional listener or configure() call is needed.
        """
        cls._init_fonts()
        return cls._fonts.get(role, cls._fonts["normal"])

    @classmethod
    def size(cls) -> int:
        return cls._size

    # ------------------------------------------------------------------
    # Change
    # ------------------------------------------------------------------

    @classmethod
    def increase(cls):
        if cls._size < _MAX_SIZE:
            cls._size += 1
            cls._notify()

    @classmethod
    def decrease(cls):
        if cls._size > _MIN_SIZE:
            cls._size -= 1
            cls._notify()

    @classmethod
    def set_size(cls, size: int):
        """Set font size to an explicit value, clamped to valid range."""
        new = max(_MIN_SIZE, min(_MAX_SIZE, int(size)))
        if new != cls._size:
            cls._size = new
            cls._notify()

    @classmethod
    def reset(cls):
        cls._size = _DEFAULT_SIZE
        cls._notify()

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------

    @classmethod
    def add_listener(cls, fn: Callable[[], None]):
        """Register a callback invoked whenever font size changes.

        Listeners are still useful for non-font updates (e.g. Treeview
        rowheight).  For font-only updates, Font objects are sufficient.
        """
        if fn not in cls._listeners:
            cls._listeners.append(fn)

    @classmethod
    def remove_listener(cls, fn: Callable[[], None]):
        if fn in cls._listeners:
            cls._listeners.remove(fn)

    @classmethod
    def _notify(cls):
        # 1. Reconfigure live Font objects — auto-updates all widgets.
        cls._reconfigure_fonts()
        # 2. Call any registered listeners (e.g. for rowheight etc.).
        for fn in list(cls._listeners):
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Apply globally to ttk styles
    # ------------------------------------------------------------------

    @classmethod
    def apply_to_style(cls, style: "ttk.Style"):  # type: ignore[name-defined]
        """
        Push current size into all ttk widget styles AND into the tkinter
        named-font objects.

        Updating the named fonts covers plain-tk widgets that use the system
        default (no explicit font= set).  The style.configure() calls handle
        Treeview rowheight and other ttk-specific properties.
        """
        s      = cls._size
        normal = cls.get("normal")
        bold   = cls.get("bold")

        # ── 1. Resize the Tk named-font objects ─────────────────────────
        # Covers widgets created without an explicit font= argument.
        _named = [
            ("TkDefaultFont",       s),
            ("TkTextFont",          s),
            ("TkMenuFont",          s),
            ("TkHeadingFont",       s),
            ("TkCaptionFont",       s),
            ("TkFixedFont",         s),
            ("TkIconFont",          s),
            ("TkTooltipFont",       s),
            ("TkSmallCaptionFont",  max(s - 2, _MIN_SIZE)),
        ]
        for name, size in _named:
            try:
                tkfont.nametofont(name).configure(size=size)
            except Exception:
                pass

        # ── 2. ttk style overrides ───────────────────────────────────────
        style.configure(".",                 font=normal)
        style.configure("TButton",           font=normal)
        style.configure("TLabel",            font=normal)
        style.configure("TEntry",            font=normal)
        style.configure("TCombobox",         font=normal)
        style.configure("TCheckbutton",      font=normal)
        style.configure("TRadiobutton",      font=normal)
        style.configure("TLabelframe.Label", font=normal)
        style.configure("TNotebook.Tab",     font=normal)
        style.configure("Treeview",          font=normal, rowheight=int(s * 2.2))
        style.configure("Treeview.Heading",  font=bold)
        style.configure("Accent.TButton",    font=bold)

        # ── 3. Combobox popup listbox ────────────────────────────────────
        try:
            root = style.master
            root.option_add("*TCombobox*Listbox.font", normal, "interactive")
        except Exception:
            pass
