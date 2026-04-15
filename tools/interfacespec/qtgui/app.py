"""
MainWindow — root Tk window.

Layout:
  ┌──────────────────────────────────────────────┐
  │  InterfaceSpec Pipeline Tool     A- 10 A+ ↺  │
  ├──────────────────────────────────────────────┤
  │  [Pipeline]  [Interface Spec]  [Results]     │
  │                                              │
  │  <tab content>                               │
  └──────────────────────────────────────────────┘
"""

import tkinter as tk
from tkinter import ttk

from .font_manager  import FontManager
from .theme_manager import ThemeManager
from .tabs.pipeline_tab import PipelineTab
from .tabs.results_tab  import ResultsTab
from .tabs.spec_tab     import SpecTab
from .utils             import load_settings, save_settings, cleanup_settings


class MainApp:
    """
    Creates and manages the main application window.
    """

    TITLE  = "InterfaceSpec Pipeline Tool"
    WIDTH  = 1200
    HEIGHT = 820

    def __init__(self, root: tk.Tk):
        self._root = root
        root.title(self.TITLE)
        root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        root.minsize(900, 600)

        self._style = ttk.Style(root)
        self._apply_theme()

        self._autosave_pending = None  # after() id for debounced save

        self._build_ui()
        self._load_settings()
        self._watch_settings_changes()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Theme / style
    # ------------------------------------------------------------------

    def _apply_theme(self):
        available = self._style.theme_names()
        for preferred in ("clam", "alt", "default"):
            if preferred in available:
                self._style.theme_use(preferred)
                break
        ThemeManager.apply_to_style(self._style)   # colors first
        ThemeManager.apply_to_root(self._root)     # plain-tk defaults
        FontManager.apply_to_style(self._style)    # fonts on top

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Header bar ─────────────────────────────────────────────────
        self._header = tk.Frame(self._root, bg=ThemeManager.get("hdr_bg"), pady=4)
        self._header.pack(fill=tk.X)

        self._header_title = tk.Label(
            self._header,
            text=self.TITLE,
            font=FontManager.get("heading"),
            bg=ThemeManager.get("hdr_bg"),
            fg=ThemeManager.get("hdr_fg"),
            padx=12,
        )
        self._header_title.pack(side=tk.LEFT)

        self._build_font_controls(self._header)

        # ── Main notebook ───────────────────────────────────────────────
        self._nb = ttk.Notebook(self._root)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        # Tab 3 must exist before Tab 1 creates its callback
        self._results_tab = ResultsTab(self._nb)

        # Tab 1 — Pipeline
        self._pipeline_tab = PipelineTab(
            self._nb,
            on_results_ready=self._on_results_ready,
        )

        # Tab 2 — Interface Spec
        self._spec_tab = SpecTab(
            self._nb,
            on_spec_ready=self._on_spec_ready,
        )

        self._nb.add(self._pipeline_tab, text="  Pipeline  ")
        self._nb.add(self._spec_tab,     text="  Interface Spec  ")
        self._nb.add(self._results_tab,  text="  Results  ")

    def _build_font_controls(self, header: tk.Frame):
        """A− [size] A+ ↺ and Theme controls in the header bar."""
        ctrl = tk.Frame(header, bg=ThemeManager.get("hdr_bg"))
        ctrl.pack(side=tk.RIGHT, padx=12)

        btn_cfg = dict(
            bg=ThemeManager.get("accent_btn_bg"),
            fg=ThemeManager.get("hdr_fg"),
            relief=tk.FLAT,
            activebackground=ThemeManager.get("sel_bg"),
            activeforeground=ThemeManager.get("sel_fg"),
            bd=0, padx=6, pady=2, cursor="hand2",
        )

        self._hdr_buttons: list = []  # keep refs for theme update

        b_dec = tk.Button(ctrl, text="A−", command=self._font_decrease, **btn_cfg)
        b_dec.pack(side=tk.LEFT)
        self._hdr_buttons.append(b_dec)

        self._font_size_var = tk.IntVar(value=FontManager.size())
        self._font_size_spin = tk.Spinbox(
            ctrl, from_=7, to=22, width=3,
            textvariable=self._font_size_var,
            command=self._apply_font_entry,
            bg=ThemeManager.get("hdr_bg"),
            fg=ThemeManager.get("accent_fg"),
            buttonbackground=ThemeManager.get("accent_btn_bg"),
            relief=tk.FLAT, bd=0,
            font=FontManager.get("bold"),
        )
        self._font_size_spin.pack(side=tk.LEFT, padx=4, ipady=2)
        self._font_size_spin.bind("<Return>",   lambda e: self._apply_font_entry())
        self._font_size_spin.bind("<FocusOut>", lambda e: self._apply_font_entry())

        b_inc = tk.Button(ctrl, text="A+", command=self._font_increase, **btn_cfg)
        b_inc.pack(side=tk.LEFT)
        self._hdr_buttons.append(b_inc)


        # ── Theme picker ────────────────────────────────────────────────
        self._theme_label = tk.Label(ctrl, text="  Theme:", bg=ThemeManager.get("hdr_bg"),
                 fg=ThemeManager.get("hdr_fg"),
                 font=FontManager.get("normal"))
        self._theme_label.pack(side=tk.LEFT, padx=(12, 2))

        self._theme_var = tk.StringVar(value=ThemeManager.name())
        self._theme_cb = ttk.Combobox(
            ctrl, textvariable=self._theme_var,
            values=ThemeManager.names(), state="readonly", width=13,
        )
        self._theme_cb.pack(side=tk.LEFT, padx=(0, 4))
        self._theme_cb.bind("<<ComboboxSelected>>",
                            lambda e: self._set_theme(self._theme_var.get()))

        # Register listener for later theme changes
        ThemeManager.add_listener(self._on_theme_change)

    # ------------------------------------------------------------------
    # Font size handlers
    # ------------------------------------------------------------------

    def _font_increase(self):
        FontManager.increase()
        self._on_font_change()

    def _font_decrease(self):
        FontManager.decrease()
        self._on_font_change()

    def _font_reset(self):
        FontManager.reset()
        self._on_font_change()

    def _apply_font_entry(self):
        """Called when user types a number or uses spinbox arrows."""
        try:
            v = int(self._font_size_var.get())
            FontManager.set_size(v)   # clamps + calls _notify() → updates fonts & listeners
        except (ValueError, tk.TclError):
            pass
        self._on_font_change()

    def _on_font_change(self):
        FontManager.apply_to_style(self._style)
        self._font_size_var.set(FontManager.size())
        try:
            self._font_size_spin.configure(font=FontManager.get("bold"))
            self._theme_label.configure(font=FontManager.get("normal"))
            self._theme_cb.configure(font=FontManager.get("normal"))
        except Exception:
            pass

    def _set_theme(self, name: str):
        ThemeManager.set_theme(name)
        ThemeManager.apply_to_style(self._style)
        ThemeManager.apply_to_root(self._root)
        FontManager.apply_to_style(self._style)   # keep fonts after color reset
        ThemeManager.restyle_widget_tree(self._root)

    def _on_theme_change(self):
        """Re-configure plain tk header widgets when the theme switches."""
        hdr_bg   = ThemeManager.get("hdr_bg")
        hdr_fg   = ThemeManager.get("hdr_fg")
        btn_bg   = ThemeManager.get("accent_btn_bg")
        sel_bg   = ThemeManager.get("sel_bg")
        sel_fg   = ThemeManager.get("sel_fg")
        acc_fg   = ThemeManager.get("accent_fg")
        try:
            self._header.configure(bg=hdr_bg)
            self._header_title.configure(bg=hdr_bg, fg=hdr_fg)
            for btn in self._hdr_buttons:
                btn.configure(bg=btn_bg, fg=hdr_fg,
                              activebackground=sel_bg, activeforeground=sel_fg)
            self._font_size_spin.configure(
                bg=hdr_bg, fg=acc_fg, buttonbackground=btn_bg)
            self._theme_label.configure(bg=hdr_bg, fg=hdr_fg)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Callbacks from tabs
    # ------------------------------------------------------------------

    def _on_results_ready(self, cluster: str, result_dir: str, result_paths: dict = None):
        """Called by PipelineTab after analysis completes."""
        model_root = self._pipeline_tab.get_model_root()
        self._spec_tab.set_result_dir(result_dir)
        self._spec_tab.set_model_root(model_root)
        self._spec_tab.set_cluster(cluster)
        self._results_tab.load_results(result_dir, model_root=model_root,
                                       out_root=result_dir)
        self._nb.select(self._results_tab)

    def _on_spec_ready(self, module: str, out_md: str):
        """Called by SpecTab after spec generation completes."""
        self._results_tab.load_spec_md(module, out_md)
        self._nb.select(self._results_tab)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _collect_settings(self) -> dict:
        settings = {
            "font_size": FontManager.size(),
            "theme":     ThemeManager.name(),
        }
        settings.update(self._pipeline_tab.collect_settings())
        settings.update(self._spec_tab.collect_settings())
        return settings

    def _watch_settings_changes(self):
        """Attach traces to all form variables so settings auto-save
        ~2 s after the last change (debounced).  This ensures settings
        survive crashes and Ctrl-C kills."""
        for tab in (self._pipeline_tab, self._spec_tab):
            for var in tab.settings_vars():
                var.trace_add("write", self._on_settings_change)

    def _on_settings_change(self, *_):
        """Debounce: cancel any pending save and schedule a new one."""
        if self._autosave_pending is not None:
            try:
                self._root.after_cancel(self._autosave_pending)
            except Exception:
                pass
        self._autosave_pending = self._root.after(2000, self._autosave)

    def _autosave(self):
        self._autosave_pending = None
        save_settings(self._collect_settings())

    def _load_settings(self):
        settings = load_settings()
        # Restore font size
        if "font_size" in settings:
            FontManager.set_size(int(settings["font_size"]))  # clamps + notifies listeners
            self._on_font_change()
        # Restore theme
        if "theme" in settings:
            name = settings["theme"]
            if name in ThemeManager.names():
                self._theme_var.set(name)
                self._set_theme(name)
        self._pipeline_tab.apply_settings(settings)
        self._spec_tab.apply_settings(settings)
        # Pre-populate model_root in results tab for path resolution
        if "model_root" in settings:
            self._results_tab.set_model_root(settings["model_root"])

    def _on_close(self):
        save_settings(self._collect_settings())
        cleanup_settings()          # remove our per-process file on clean exit
        # Deregister listener to avoid calls on destroyed widgets
        try:
            ThemeManager.remove_listener(self._on_theme_change)
        except Exception:
            pass
        self._root.destroy()
