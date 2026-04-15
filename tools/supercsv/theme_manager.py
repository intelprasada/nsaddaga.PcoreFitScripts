"""
ThemeManager — application-wide color theme control.

Provides a set of gvim-inspired ttk themes (Light, Monokai, Solarized Dark,
Solarized Light, Dracula) that can be switched at runtime.

Usage pattern mirrors FontManager:
  1. Call ThemeManager.set_theme(name) from a toolbar combobox.
  2. Call ThemeManager.apply_to_style(style) to push colors into ttk.Style.
  3. Call ThemeManager.apply_to_root(root)  to set tk option defaults.
  4. Register callbacks via ThemeManager.add_listener(fn) so widgets can
     re-configure their non-ttk children when the theme changes.

Color tokens
------------
  bg               Main background (frames, windows)
  fg               Normal foreground text
  row_even_bg      Even Treeview row background
  row_odd_bg       Odd Treeview row background (alternating stripe)
  sel_bg           Selection / highlight background
  sel_fg           Selection foreground
  hdr_bg           Column-header / accent-bar background
  hdr_fg           Column-header / accent-bar foreground
  accent_bg        Accent colour (same as hdr_bg in most themes)
  accent_fg        Accent foreground
  accent_btn_bg    Button background placed on accent bars
  entry_bg         Entry / Text / Listbox background
  entry_fg         Entry / Text / Listbox foreground
  entry_insert     Insertion-cursor colour in Entry / Text
  filter_active_bg Filter-entry background when the filter is non-empty
  filter_idle_bg   Filter-entry background when empty
  hint_fg          Muted hint / label foreground
  dim_fg           Dim label foreground (slightly lighter than hint)
  section_bg       Section-header row background in summary trees
  terminal_bg      Dark terminal output background (fixed across themes)
  terminal_fg      Dark terminal output foreground (fixed across themes)
  ok_fg            Status-good / resolved foreground
  warn_fg          Status-warning foreground
  err_fg           Status-error / unresolved foreground
  email_fg         E-mail address highlight foreground
  name_fg          Display-name muted foreground
  label_fg         Dim label prefix foreground (e.g. "To:", "CC:")
  scroll_trough    Scrollbar track / channel background
  scroll_thumb     Scrollbar draggable thumb colour
  scroll_thumb_act Scrollbar thumb colour when hovered / active
"""

from typing import Callable, Dict, List

# ── Theme definitions ─────────────────────────────────────────────────────────

THEMES: Dict[str, Dict[str, str]] = {
    "Light": {
        "bg":               "#f0f0f0",
        "fg":               "#111111",
        "row_even_bg":      "#ffffff",
        "row_odd_bg":       "#ddeeff",
        "sel_bg":           "#1565c0",
        "sel_fg":           "#ffffff",
        "hdr_bg":           "#1a237e",
        "hdr_fg":           "#ffffff",
        "accent_bg":        "#1a237e",
        "accent_fg":        "#ffffff",
        "accent_btn_bg":    "#283593",
        "entry_bg":         "#ffffff",
        "entry_fg":         "#222222",
        "entry_insert":     "#222222",
        "filter_active_bg": "#fff9c4",
        "filter_idle_bg":   "#f5f5f5",
        "hint_fg":          "#555577",
        "dim_fg":           "#666666",
        "section_bg":       "#cce0ff",
        "terminal_bg":      "#0d1117",
        "terminal_fg":      "#c9d1d9",
        "ok_fg":            "#2e7d32",
        "warn_fg":          "#e65100",
        "err_fg":           "#c62828",
        "email_fg":         "#1565c0",
        "name_fg":          "#777777",
        "label_fg":         "#888888",
        "scroll_trough":    "#d0d0d0",
        "scroll_thumb":     "#7a7a7a",
        "scroll_thumb_act": "#404040",
    },
    "Monokai": {
        "bg":               "#272822",
        "fg":               "#f8f8f2",
        "row_even_bg":      "#272822",
        "row_odd_bg":       "#3e3d32",
        "sel_bg":           "#a6e22e",
        "sel_fg":           "#272822",
        "hdr_bg":           "#75715e",
        "hdr_fg":           "#f8f8f2",
        "accent_bg":        "#75715e",
        "accent_fg":        "#f8f8f2",
        "accent_btn_bg":    "#49483e",
        "entry_bg":         "#3e3d32",
        "entry_fg":         "#f8f8f2",
        "entry_insert":     "#f8f8f2",
        "filter_active_bg": "#49483e",
        "filter_idle_bg":   "#3e3d32",
        "hint_fg":          "#75715e",
        "dim_fg":           "#75715e",
        "section_bg":       "#49483e",
        "terminal_bg":      "#1e1f1c",
        "terminal_fg":      "#f8f8f2",
        "ok_fg":            "#a6e22e",
        "warn_fg":          "#e6db74",
        "err_fg":           "#f92672",
        "email_fg":         "#66d9e8",
        "name_fg":          "#75715e",
        "label_fg":         "#75715e",
        "scroll_trough":    "#1e1f1c",
        "scroll_thumb":     "#75715e",
        "scroll_thumb_act": "#a6e22e",
    },
    "Solarized Dark": {
        "bg":               "#002b36",
        "fg":               "#839496",
        "row_even_bg":      "#002b36",
        "row_odd_bg":       "#073642",
        "sel_bg":           "#268bd2",
        "sel_fg":           "#fdf6e3",
        "hdr_bg":           "#073642",
        "hdr_fg":           "#93a1a1",
        "accent_bg":        "#073642",
        "accent_fg":        "#93a1a1",
        "accent_btn_bg":    "#002b36",
        "entry_bg":         "#073642",
        "entry_fg":         "#839496",
        "entry_insert":     "#93a1a1",
        "filter_active_bg": "#073642",
        "filter_idle_bg":   "#002b36",
        "hint_fg":          "#586e75",
        "dim_fg":           "#586e75",
        "section_bg":       "#073642",
        "terminal_bg":      "#001e26",
        "terminal_fg":      "#839496",
        "ok_fg":            "#859900",
        "warn_fg":          "#b58900",
        "err_fg":           "#dc322f",
        "email_fg":         "#268bd2",
        "name_fg":          "#586e75",
        "label_fg":         "#586e75",
        "scroll_trough":    "#001e26",
        "scroll_thumb":     "#586e75",
        "scroll_thumb_act": "#268bd2",
    },
    "Solarized Light": {
        "bg":               "#fdf6e3",
        "fg":               "#657b83",
        "row_even_bg":      "#fdf6e3",
        "row_odd_bg":       "#eee8d5",
        "sel_bg":           "#268bd2",
        "sel_fg":           "#fdf6e3",
        "hdr_bg":           "#073642",
        "hdr_fg":           "#839496",
        "accent_bg":        "#073642",
        "accent_fg":        "#839496",
        "accent_btn_bg":    "#002b36",
        "entry_bg":         "#eee8d5",
        "entry_fg":         "#586e75",
        "entry_insert":     "#586e75",
        "filter_active_bg": "#fffbf0",
        "filter_idle_bg":   "#eee8d5",
        "hint_fg":          "#93a1a1",
        "dim_fg":           "#93a1a1",
        "section_bg":       "#d0e4f0",
        "terminal_bg":      "#002b36",
        "terminal_fg":      "#839496",
        "ok_fg":            "#859900",
        "warn_fg":          "#b58900",
        "err_fg":           "#dc322f",
        "email_fg":         "#268bd2",
        "name_fg":          "#93a1a1",
        "label_fg":         "#93a1a1",
        "scroll_trough":    "#ddd6c1",
        "scroll_thumb":     "#93a1a1",
        "scroll_thumb_act": "#268bd2",
    },
    "Dracula": {
        "bg":               "#282a36",
        "fg":               "#f8f8f2",
        "row_even_bg":      "#282a36",
        "row_odd_bg":       "#44475a",
        "sel_bg":           "#bd93f9",
        "sel_fg":           "#f8f8f2",
        "hdr_bg":           "#44475a",
        "hdr_fg":           "#ff79c6",
        "accent_bg":        "#44475a",
        "accent_fg":        "#ff79c6",
        "accent_btn_bg":    "#6272a4",
        "entry_bg":         "#44475a",
        "entry_fg":         "#f8f8f2",
        "entry_insert":     "#f8f8f2",
        "filter_active_bg": "#6272a4",
        "filter_idle_bg":   "#44475a",
        "hint_fg":          "#6272a4",
        "dim_fg":           "#6272a4",
        "section_bg":       "#6272a4",
        "terminal_bg":      "#1a1a2e",
        "terminal_fg":      "#69f0ae",
        "ok_fg":            "#50fa7b",
        "warn_fg":          "#ffb86c",
        "err_fg":           "#ff5555",
        "email_fg":         "#8be9fd",
        "name_fg":          "#6272a4",
        "label_fg":         "#6272a4",
        "scroll_trough":    "#1e1f2b",
        "scroll_thumb":     "#6272a4",
        "scroll_thumb_act": "#bd93f9",
    },
    # "Default" is a sentinel theme: apply_to_style calls style.theme_use("default")
    # and returns early; apply_to_root calls option_clear() so that plain tk
    # widgets use the native platform colors.  The color values below are used
    # only by restyle_widget_tree() for any plain-tk widgets that need
    # explicit configure() calls after a theme switch.
    "Default": {
        "bg":               "#d9d9d9",
        "fg":               "#000000",
        "row_even_bg":      "#ffffff",
        "row_odd_bg":       "#f0f0f0",
        "sel_bg":           "#0078d7",
        "sel_fg":           "#ffffff",
        "hdr_bg":           "#d9d9d9",
        "hdr_fg":           "#000000",
        "accent_bg":        "#d9d9d9",
        "accent_fg":        "#000000",
        "accent_btn_bg":    "#c0c0c0",
        "entry_bg":         "#ffffff",
        "entry_fg":         "#000000",
        "entry_insert":     "#000000",
        "filter_active_bg": "#fffacd",
        "filter_idle_bg":   "#ffffff",
        "hint_fg":          "#555555",
        "dim_fg":           "#555555",
        "section_bg":       "#e0e0e0",
        "terminal_bg":      "#0d1117",
        "terminal_fg":      "#c9d1d9",
        "ok_fg":            "#007700",
        "warn_fg":          "#b35900",
        "err_fg":           "#cc0000",
        "email_fg":         "#0066cc",
        "name_fg":          "#555555",
        "label_fg":         "#777777",
        "scroll_trough":    "#c0c0c0",
        "scroll_thumb":     "#a0a0a0",
        "scroll_thumb_act": "#707070",
    },
}

_DEFAULT_THEME = "Light"


class ThemeManager:
    """Singleton managing application-wide color theme."""

    _theme: str = _DEFAULT_THEME
    _listeners: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Token accessors
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, token: str, fallback: str = "#000000") -> str:
        """Return the color hex string for *token* in the current theme."""
        return THEMES.get(cls._theme, THEMES[_DEFAULT_THEME]).get(token, fallback)

    @classmethod
    def tokens(cls) -> Dict[str, str]:
        """Return the full token dict for the current theme."""
        return dict(THEMES.get(cls._theme, THEMES[_DEFAULT_THEME]))

    @classmethod
    def name(cls) -> str:
        """Return the active theme name."""
        return cls._theme

    @classmethod
    def names(cls) -> List[str]:
        """Return the list of available theme names."""
        return list(THEMES.keys())

    # ------------------------------------------------------------------
    # Change
    # ------------------------------------------------------------------

    @classmethod
    def set_theme(cls, name: str):
        """Switch to *name* and notify all listeners."""
        if name not in THEMES:
            return
        cls._theme = name
        cls._notify()

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------

    @classmethod
    def add_listener(cls, fn: Callable[[], None]):
        """Register *fn* to be called whenever the theme changes."""
        if fn not in cls._listeners:
            cls._listeners.append(fn)

    @classmethod
    def remove_listener(cls, fn: Callable[[], None]):
        if fn in cls._listeners:
            cls._listeners.remove(fn)

    @classmethod
    def _notify(cls):
        for fn in list(cls._listeners):
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Apply to ttk.Style
    # ------------------------------------------------------------------

    @classmethod
    def apply_to_style(cls, style: "ttk.Style"):  # type: ignore[name-defined]
        """
        Push the current theme's colors into *style*.

        Only color properties are set here; font/rowheight properties are
        left to FontManager.apply_to_style() so both can co-exist without
        either overwriting the other's settings.

        When the "Default" theme is active the native ttk "default" theme is
        applied and all color overrides are skipped so widgets render with the
        platform's built-in appearance.
        """
        if cls._theme == "Default":
            try:
                style.theme_use("default")
            except Exception:
                pass
            return

        # For all other named themes ensure we are on the "clam" base so that
        # switching away from "Default" properly resets ttk widget styling.
        try:
            style.theme_use("clam")
        except Exception:
            pass

        t = THEMES.get(cls._theme, THEMES[_DEFAULT_THEME])
        bg        = t["bg"]
        fg        = t["fg"]
        entry_bg  = t["entry_bg"]
        entry_fg  = t["entry_fg"]
        hdr_bg    = t["hdr_bg"]
        hdr_fg    = t["hdr_fg"]
        sel_bg    = t["sel_bg"]
        sel_fg    = t["sel_fg"]
        even_bg   = t["row_even_bg"]

        # Global defaults
        style.configure(".",
                         background=bg,
                         foreground=fg,
                         troughcolor=bg,
                         selectbackground=sel_bg,
                         selectforeground=sel_fg,
                         insertcolor=entry_fg)

        # Common widgets
        style.configure("TFrame",     background=bg)
        style.configure("TLabel",     background=bg,        foreground=fg)
        style.configure("TLabelframe",       background=bg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TSeparator",        background=fg)
        style.configure("TCheckbutton",      background=bg, foreground=fg)
        style.configure("TRadiobutton",      background=bg, foreground=fg)

        # Buttons
        style.configure("TButton",
                         background=hdr_bg, foreground=hdr_fg,
                         bordercolor=bg, lightcolor=hdr_bg, darkcolor=hdr_bg,
                         focuscolor=sel_bg)
        style.map("TButton",
                  background=[("active", sel_bg),   ("disabled", bg)],
                  foreground=[("active", sel_fg),   ("disabled", t["dim_fg"])])

        # Accent button (used for primary actions)
        style.configure("Accent.TButton",
                         background=sel_bg, foreground=sel_fg,
                         bordercolor=bg, lightcolor=sel_bg, darkcolor=sel_bg)
        style.map("Accent.TButton",
                  background=[("active", hdr_bg)],
                  foreground=[("active", hdr_fg)])

        # Entry / Combobox / Spinbox
        style.configure("TEntry",
                         fieldbackground=entry_bg, foreground=entry_fg,
                         insertcolor=t["entry_insert"],
                         selectbackground=sel_bg, selectforeground=sel_fg,
                         bordercolor=hdr_bg)
        style.configure("TCombobox",
                         fieldbackground=entry_bg, foreground=entry_fg,
                         selectbackground=sel_bg, selectforeground=sel_fg,
                         arrowcolor=fg)
        style.configure("TSpinbox",
                         fieldbackground=entry_bg, foreground=entry_fg,
                         selectbackground=sel_bg, selectforeground=sel_fg,
                         arrowcolor=fg)

        # Notebook tabs
        style.configure("TNotebook",     background=bg, tabmargins=[2, 5, 2, 0])
        style.configure("TNotebook.Tab", background=bg, foreground=fg,
                         padding=[8, 2])
        style.map("TNotebook.Tab",
                  background=[("selected", hdr_bg)],
                  foreground=[("selected", hdr_fg)])

        # Scrollbars — doubled width (arrowsize) and visible thumb/trough colors
        scroll_trough    = t["scroll_trough"]
        scroll_thumb     = t["scroll_thumb"]
        scroll_thumb_act = t["scroll_thumb_act"]
        style.configure("TScrollbar",
                         background=scroll_thumb,
                         troughcolor=scroll_trough,
                         arrowcolor=fg,
                         bordercolor=scroll_trough,
                         lightcolor=scroll_thumb,
                         darkcolor=scroll_thumb,
                         arrowsize=16,
                         relief="flat")
        style.map("TScrollbar",
                  background=[("active",  scroll_thumb_act),
                               ("pressed", scroll_thumb_act)])

        # Treeview
        style.configure("Treeview",
                         background=even_bg,
                         foreground=fg,
                         fieldbackground=even_bg,
                         bordercolor=bg)
        style.configure("Treeview.Heading",
                         background=hdr_bg, foreground=hdr_fg,
                         relief="flat", borderwidth=0)
        style.map("Treeview",
                  background=[("selected", sel_bg)],
                  foreground=[("selected", sel_fg)])
        style.map("Treeview.Heading",
                  background=[("active", sel_bg)],
                  foreground=[("active", sel_fg)])

    # ------------------------------------------------------------------
    # Apply tk option defaults to root window
    # ------------------------------------------------------------------

    @classmethod
    def apply_to_root(cls, root):
        """
        Set tk option-database defaults on *root* so that plain tk widgets
        (tk.Label, tk.Entry, tk.Text, tk.Listbox, tk.Menu, etc.) that are
        created AFTER this call will inherit the theme colors.

        For already-created plain tk widgets call _restyle_widget_tree()
        or handle them individually in _on_theme_change() listeners.

        When the "Default" theme is active the option-database is cleared so
        that all plain-tk widgets fall back to the platform's native colors.
        """
        if cls._theme == "Default":
            try:
                root.option_clear()
            except Exception:
                pass
            return

        t = THEMES.get(cls._theme, THEMES[_DEFAULT_THEME])
        bg       = t["bg"]
        fg       = t["fg"]
        entry_bg = t["entry_bg"]
        entry_fg = t["entry_fg"]
        sel_bg   = t["sel_bg"]
        sel_fg   = t["sel_fg"]

        P = "interactive"   # priority level

        root.option_add("*Background",               bg,       P)
        root.option_add("*Foreground",               fg,       P)
        root.option_add("*activeBackground",         sel_bg,   P)
        root.option_add("*activeForeground",         sel_fg,   P)
        root.option_add("*disabledForeground",       t["dim_fg"], P)

        root.option_add("*Entry.Background",         entry_bg, P)
        root.option_add("*Entry.Foreground",         entry_fg, P)
        root.option_add("*Entry.InsertBackground",   t["entry_insert"], P)
        root.option_add("*Entry.SelectBackground",   sel_bg,   P)
        root.option_add("*Entry.SelectForeground",   sel_fg,   P)

        root.option_add("*Text.Background",          entry_bg, P)
        root.option_add("*Text.Foreground",          entry_fg, P)
        root.option_add("*Text.InsertBackground",    t["entry_insert"], P)
        root.option_add("*Text.SelectBackground",    sel_bg,   P)
        root.option_add("*Text.SelectForeground",    sel_fg,   P)

        root.option_add("*Listbox.Background",       entry_bg, P)
        root.option_add("*Listbox.Foreground",       entry_fg, P)
        root.option_add("*Listbox.SelectBackground", sel_bg,   P)
        root.option_add("*Listbox.SelectForeground", sel_fg,   P)

        root.option_add("*Menu.Background",          bg,       P)
        root.option_add("*Menu.Foreground",          fg,       P)
        root.option_add("*Menu.ActiveBackground",    sel_bg,   P)
        root.option_add("*Menu.ActiveForeground",    sel_fg,   P)

        # Combobox popup listbox
        root.option_add("*TCombobox*Listbox.Background", entry_bg, P)
        root.option_add("*TCombobox*Listbox.Foreground", entry_fg, P)

    # ------------------------------------------------------------------
    # Convenience: restyle existing plain-tk widget subtree
    # ------------------------------------------------------------------

    @classmethod
    def restyle_widget_tree(cls, widget):
        """
        Walk *widget* and its children and re-configure colors on plain
        tk widgets (Frame, Label, Button, Entry, Text, Listbox, Canvas).

        ttk widgets are handled by apply_to_style(); only plain tk widgets
        need explicit configure() calls after a theme switch.

        Widgets whose class name starts with 'T' (ttk) are skipped.
        """
        t = THEMES.get(cls._theme, THEMES[_DEFAULT_THEME])
        cls._restyle_one(widget, t)
        for child in widget.winfo_children():
            cls.restyle_widget_tree(child)

    @classmethod
    def _restyle_one(cls, w, t: Dict[str, str]):
        cls_name = w.winfo_class()
        # Skip ttk widgets — handled by apply_to_style
        if cls_name.startswith("T") and cls_name != "Text":
            return
        bg       = t["bg"]
        fg       = t["fg"]
        entry_bg = t["entry_bg"]
        entry_fg = t["entry_fg"]
        sel_bg   = t["sel_bg"]
        sel_fg   = t["sel_fg"]
        ins      = t["entry_insert"]
        try:
            if cls_name in ("Frame", "Labelframe", "Canvas"):
                w.configure(bg=bg)
            elif cls_name in ("Label", "Button", "Checkbutton",
                              "Radiobutton", "Menubutton"):
                w.configure(bg=bg, fg=fg,
                            activebackground=sel_bg, activeforeground=sel_fg)
            elif cls_name in ("Entry", "Spinbox"):
                w.configure(bg=entry_bg, fg=entry_fg,
                            insertbackground=ins,
                            selectbackground=sel_bg, selectforeground=sel_fg)
            elif cls_name == "Text":
                w.configure(bg=entry_bg, fg=entry_fg,
                            insertbackground=ins,
                            selectbackground=sel_bg, selectforeground=sel_fg)
            elif cls_name == "Listbox":
                w.configure(bg=entry_bg, fg=entry_fg,
                            selectbackground=sel_bg, selectforeground=sel_fg)
        except Exception:
            pass
