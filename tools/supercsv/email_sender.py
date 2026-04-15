"""
email_sender — reusable email widget that can attach any file(s).

Provides
--------
    EmailDialog                  – modal Toplevel; recipient form, attachment
                                   summary, live command preview, output log.
    send_email_with_attachment() – one-liner to open the dialog.
    make_email_button()          – factory returning a ready-to-use Button.

Attachment types accepted  (``attach`` parameter)
-------------------------------------------------
    str            – path to a single file (any type: csv, pdf, log, ...)
    list[str]      – paths to one or more files
    pd.DataFrame   – written to a temp CSV just before sending
    None           – send body-only, no attachment

For backward compatibility the keyword ``df=`` is still accepted as an alias
for a single DataFrame attachment.  pandas itself is also a soft dependency;
the module works without it as long as no DataFrame is passed.

Recipient handling
------------------
Bare IDs (e.g. ``nsaddaga``) are expanded to ``nsaddaga@intel.com``.
Multiple IDs/addresses may be comma-separated in both To and CC fields.

Backend
-------
Sending is attempted with **mutt** first, **mailx** as fallback.
The exact shell command is shown in the dialog for terminal debugging.

FontManager
-----------
Soft dependency on ``scripts/supercsv/font_manager.py``.  A minimal
fallback is used automatically when not found.

Usage examples
--------------
    # Any single file
    send_email_with_attachment(parent, attach="/tmp/report.pdf",
                               default_subject="Weekly report")

    # Multiple files
    send_email_with_attachment(parent,
                               attach=["/tmp/a.csv", "/tmp/b.log"])

    # DataFrame -> temp CSV on send
    send_email_with_attachment(parent, attach=my_df)

    # body only
    send_email_with_attachment(parent, default_subject="Hello")

    # Toolbar button
    btn = make_email_button(
        toolbar,
        get_attach  = lambda: "/tmp/results.csv",
        get_subject = lambda: "Results",
    )
    btn.pack(side=tk.RIGHT, padx=4)
"""

import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional, Union

# ---- pandas (soft dependency) -----------------------------------------------
try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:
    _PANDAS_OK = False
    pd = None  # type: ignore[assignment]

# ---- FontManager (soft dependency from scripts/supercsv/) -------------------
_here = os.path.dirname(os.path.abspath(__file__))
_sc   = os.path.normpath(os.path.join(_here, "..", "supercsv"))
if os.path.isdir(_sc) and _sc not in sys.path:
    sys.path.insert(0, _sc)

try:
    from font_manager import FontManager  # type: ignore[import]
except ImportError:
    class FontManager:  # type: ignore[no-redef]
        """Minimal fallback when font_manager.py is unavailable."""
        _size: int = 14

        @classmethod
        def get(cls, role: str = "normal"):
            s = cls._size
            if role == "mono":
                return ("Courier", s)
            if role == "bold":
                return ("TkDefaultFont", s, "bold")
            if role == "small":
                return ("TkDefaultFont", max(s - 1, 7))
            return ("TkDefaultFont", s)

        @classmethod
        def add_listener(cls, fn: Callable):
            pass

        @classmethod
        def remove_listener(cls, fn: Callable):
            pass

# ---- ThemeManager (soft dependency from scripts/supercsv/) ------------------
try:
    from theme_manager import ThemeManager as _TM  # type: ignore[import]
    _TM_OK = True
except ImportError:
    _TM_OK = False
    _TM = None  # type: ignore[assignment]


def _tc(token: str, fallback: str) -> str:
    """Safe color-token lookup — returns *fallback* when ThemeManager is absent."""
    if _TM_OK and _TM is not None:
        return _TM.get(token, fallback)
    return fallback


# Anything _resolve_attach() can handle
_AttachInput = Union[str, List[str], "pd.DataFrame", None]

_DOMAIN = "intel.com"


# =============================================================================
# Helpers
# =============================================================================

def _resolve_addr_list(raw: str) -> List[str]:
    """
    Expand comma-separated IDs/addresses to full addresses.
    Uses phonebook when available; falls back to bare @intel.com appending.
    Returns only successfully resolved addresses.
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result = []
    for p in parts:
        r = _pb_lookup(p)
        if r["email"]:
            result.append(r["email"])
    return result


# ---------------------------------------------------------------------------
# Phonebook lookup
# ---------------------------------------------------------------------------

_PHONEBOOK = "/usr/intel/bin/phonebook"

def _pb_lookup(token: str):
    """
    Look up ``token`` in the Intel phonebook.

    Returns a dict:
      {
        "status":  "ok" | "not_found" | "ambiguous" | "has_email" | "no_pb",
        "email":   str | None,          # resolved address (status=="ok")
        "name":    str | None,          # BookName from phonebook
        "matches": [(name, email), ...]  # all hits (status=="ambiguous")
      }

    "has_email"  – token already contains '@'; phonebook not queried.
    "no_pb"      – phonebook binary not available.
    """
    # ---- already a full address ------------------------------------------------
    if "@" in token:
        addr = token if "." in token.split("@")[1] else f"{token}@{_DOMAIN}"
        return {"status": "has_email", "email": addr, "name": None, "matches": []}

    # ---- phonebook not available ------------------------------------------------
    if not os.path.isfile(_PHONEBOOK):
        addr = f"{token}@{_DOMAIN}"
        return {"status": "no_pb", "email": addr, "name": None, "matches": []}

    # ---- query phonebook --------------------------------------------------------
    try:
        result = subprocess.run(
            [_PHONEBOOK, "-q", token],
            capture_output=True, text=True, timeout=8
        )
        lines = result.stdout.strip().splitlines()
    except Exception:
        addr = f"{token}@{_DOMAIN}"
        return {"status": "no_pb", "email": addr, "name": None, "matches": []}

    # First line is header: BookName \t PhoneNum \t BldgCode \t MailStop \t DomainAddress
    data_lines = [l for l in lines[1:] if l.strip()] if len(lines) > 1 else []

    matches = []
    for line in data_lines:
        cols = line.split("\t")
        # columns: BookName PhoneNum BldgCode MailStop DomainAddress
        name  = cols[0].strip() if len(cols) > 0 else ""
        email = cols[4].strip() if len(cols) > 4 else ""
        if email and email != "-":
            matches.append((name, email))

    if len(matches) == 0:
        # Fall back to bare @intel.com
        addr = f"{token}@{_DOMAIN}"
        return {"status": "not_found", "email": addr, "name": None, "matches": []}
    elif len(matches) == 1:
        name, email = matches[0]
        return {"status": "ok", "email": email, "name": name, "matches": matches}
    else:
        # Ambiguous — try exact prefix match first
        exact = [m for m in matches if m[1].startswith(token + ".") or
                 m[1].split("@")[0] == token]
        if len(exact) == 1:
            name, email = exact[0]
            return {"status": "ok", "email": email, "name": name, "matches": matches}
        return {"status": "ambiguous", "email": None, "name": None,
                "matches": matches}


def _fmt_size(nbytes: int) -> str:
    for unit, threshold in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if nbytes >= threshold:
            return f"{nbytes / threshold:.1f} {unit}"
    return f"{nbytes} B"


def _is_dataframe(obj) -> bool:
    return _PANDAS_OK and isinstance(obj, pd.DataFrame)


def _attach_summary_lines(attach: _AttachInput) -> List[str]:
    """Human-readable lines describing what will be attached."""
    if attach is None:
        return ["(no attachment — body only)"]
    if _is_dataframe(attach):
        rows, cols = len(attach), len(attach.columns)
        return [f"[DataFrame]  ->  temp .csv  ({rows:,} rows x {cols} cols)"]
    paths = [attach] if isinstance(attach, str) else list(attach)
    lines = []
    for p in paths:
        name = os.path.basename(p)
        try:
            size = _fmt_size(os.path.getsize(p))
        except OSError:
            size = "?"
        lines.append(f"{name}  ({size})   {p}")
    return lines or ["(no attachment — body only)"]


def _set_readonly_text(widget: tk.Text, content: str):
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, content)
    widget.configure(state=tk.DISABLED)


# =============================================================================
# EmailDialog
# =============================================================================

class EmailDialog(tk.Toplevel):
    """
    Modal dialog for composing and sending an email with optional attachments.

    Parameters
    ----------
    parent          : tk widget
    attach          : str | list[str] | pd.DataFrame | None
                      File path(s) to attach, a DataFrame (written to temp
                      CSV on send), or None for body-only email.
    df              : pd.DataFrame  — DEPRECATED alias for ``attach``.
    default_subject : pre-filled subject line
    extra_files     : optional list of file paths to append to the attachment
                      list (e.g. the session JSON saved for this tab).  Each
                      path is only added if the file currently exists on disk.
    """

    def __init__(self, parent,
                 attach: "_AttachInput" = None,
                 df=None,
                 default_subject: str = "",
                 default_body: str = "",
                 extra_files: "Optional[List[str]]" = None,
                 _block: bool = True,
                 **kwargs):
        super().__init__(parent, **kwargs)

        if attach is None and df is not None:
            attach = df
        self._attach = attach
        self._block = _block   # False in CLI mode (mainloop drives event loop)

        self.title("Email")
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        # Push current theme's option-database defaults into this Toplevel so
        # that any plain tk widgets created below pick up the right colors.
        if _TM_OK and _TM is not None:
            _TM.apply_to_root(self)

        pad = {"padx": 10, "pady": 4}

        # Header
        self._hdr_frame = tk.Frame(self, bg=_tc("hdr_bg", "#1a237e"))
        self._hdr_frame.pack(fill=tk.X)
        self._hdr_label = tk.Label(self._hdr_frame, text="  Send Email",
                                   bg=_tc("hdr_bg", "#1a237e"),
                                   fg=_tc("hdr_fg", "#ffffff"),
                                   font=FontManager.get("bold"))
        self._hdr_label.pack(side=tk.LEFT, **pad)

        # Form
        form = ttk.Frame(self)
        form.pack(fill=tk.BOTH, expand=False, padx=14, pady=8)

        ttk.Label(form, text="To:").grid(row=0, column=0, sticky="e", **pad)
        self._to_var = tk.StringVar(value=os.environ.get("USER", ""))
        self._to_entry = ttk.Entry(form, textvariable=self._to_var, width=52,
                                   font=FontManager.get("normal"))
        self._to_entry.grid(row=0, column=1, sticky="ew", **pad)
        self._to_var.trace_add("write", lambda *_: self._schedule_lookup())

        ttk.Label(form, text="CC:").grid(row=1, column=0, sticky="e", **pad)
        self._cc_var = tk.StringVar()
        self._cc_entry = ttk.Entry(form, textvariable=self._cc_var, width=52,
                                   font=FontManager.get("normal"))
        self._cc_entry.grid(row=1, column=1, sticky="ew", **pad)
        self._cc_var.trace_add("write", lambda *_: self._schedule_lookup())

        ttk.Label(form,
                  text="Comma-separated linux IDs or full email addresses - "
                       "validated via phonebook",
                  foreground="#666", font=FontManager.get("small"),
                  ).grid(row=2, column=1, sticky="w", padx=6, pady=(0, 4))

        ttk.Label(form, text="Subject:").grid(row=3, column=0, sticky="e", **pad)
        self._subj_var = tk.StringVar(value=default_subject)
        self._subj_entry = ttk.Entry(form, textvariable=self._subj_var, width=52,
                                     font=FontManager.get("normal"))
        self._subj_entry.grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        self._subj_var.trace_add("write", lambda *_: self._on_field_change())

        # "Message:" label + "Load from file..." button stacked in left column
        _msg_hdr = ttk.Frame(form)
        _msg_hdr.grid(row=4, column=0, sticky="ne", padx=10, pady=4)
        ttk.Label(_msg_hdr, text="Message:").pack(anchor="e")
        ttk.Button(_msg_hdr, text="Load file…",
                   command=self._load_body_file).pack(pady=(6, 0), fill=tk.X)

        self._body_text = tk.Text(form, width=52, height=4,
                                  font=FontManager.get("mono"),
                                  bg=_tc("entry_bg", "#fafafa"),
                                  fg=_tc("entry_fg", "#222222"),
                                  insertbackground=_tc("entry_insert", "#222222"),
                                  relief=tk.SOLID, bd=1)
        self._body_text.grid(row=4, column=1, columnspan=2, sticky="ew", **pad)

        # Pre-fill body if provided (programmatic or CLI --body-file)
        if default_body:
            self._body_text.insert("1.0", default_body)

        # Attachment manager — editable list of files to attach
        attach_frame = ttk.LabelFrame(form, text=" Attachments ")
        attach_frame.grid(row=5, column=0, columnspan=3, sticky="ew",
                          padx=4, pady=4)

        # Toolbar inside attachment frame
        attach_tb = ttk.Frame(attach_frame)
        attach_tb.pack(fill=tk.X, padx=4, pady=(4, 0))
        ttk.Button(attach_tb, text="+ Add files...",
                   command=self._browse_add_files).pack(side=tk.LEFT, padx=2)
        ttk.Button(attach_tb, text="- Remove selected",
                   command=self._remove_selected_attach).pack(side=tk.LEFT, padx=2)
        self._attach_count_lbl = ttk.Label(attach_tb, text="",
                                            foreground="#666",
                                            font=FontManager.get("small"))
        self._attach_count_lbl.pack(side=tk.LEFT, padx=8)

        # Listbox + scrollbar
        af_inner = ttk.Frame(attach_frame)
        af_inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._attach_lb = tk.Listbox(af_inner, height=3, selectmode=tk.EXTENDED,
                                     font=FontManager.get("mono"),
                                     bg=_tc("entry_bg", "#fafafa"),
                                     fg=_tc("entry_fg", "#222222"),
                                     selectbackground=_tc("sel_bg", "#1565c0"),
                                     selectforeground=_tc("sel_fg", "#ffffff"),
                                     highlightbackground=_tc("sel_bg", "#1565c0"),
                                     highlightcolor=_tc("sel_bg", "#1565c0"),
                                     highlightthickness=1,
                                     relief=tk.FLAT,
                                     activestyle="dotbox")
        af_vsb = ttk.Scrollbar(af_inner, orient=tk.VERTICAL,
                               command=self._attach_lb.yview)
        af_hsb = ttk.Scrollbar(af_inner, orient=tk.HORIZONTAL,
                               command=self._attach_lb.xview)
        self._attach_lb.configure(yscrollcommand=af_vsb.set,
                                   xscrollcommand=af_hsb.set)
        af_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        af_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._attach_lb.pack(fill=tk.BOTH, expand=True)

        # Populate initial attachment list from `attach` parameter
        self._attach_paths: List[str] = []
        if attach is not None:
            if isinstance(attach, str):
                init_paths = [attach]
            elif _is_dataframe(attach):
                init_paths = []   # DataFrame handled at send time
                self._attach_lb.insert(tk.END, "  [DataFrame -> temp CSV on send]")
                self._attach_count_lbl.configure(text="1 attachment")
            else:
                init_paths = [str(p) for p in attach]
            if not _is_dataframe(attach):
                for p in init_paths:
                    self._attach_paths.append(p)
                    self._attach_lb.insert(tk.END, f"  {os.path.basename(p)}   {p}")
                self._attach_count_lbl.configure(
                    text=f"{len(self._attach_paths)} attachment(s)")
        else:
            self._attach_count_lbl.configure(text="no attachments")

        # Append extra file paths (e.g. session JSON) provided by the caller.
        # Each path is only added if the file exists and is not already listed.
        if extra_files:
            for p in extra_files:
                p = str(p)
                if p and os.path.isfile(p) and p not in self._attach_paths:
                    self._attach_paths.append(p)
                    self._attach_lb.insert(tk.END, f"  {os.path.basename(p)}   {p}")
            # Recalculate the count label: DataFrame (if present) counts as 1.
            df_count = 1 if _is_dataframe(attach) else 0
            total = df_count + len(self._attach_paths)
            if total:
                self._attach_count_lbl.configure(text=f"{total} attachment(s)")

        form.columnconfigure(1, weight=1)

        # ------------------------------------------------------------------
        # Recipient validation preview
        # ------------------------------------------------------------------
        rcpt_frame = ttk.LabelFrame(self, text=" Resolved Recipients (phonebook) ")
        rcpt_frame.pack(fill=tk.X, expand=False, padx=14, pady=(0, 4))

        rcpt_toolbar = ttk.Frame(rcpt_frame)
        rcpt_toolbar.pack(fill=tk.X)
        self._rcpt_status_lbl = ttk.Label(rcpt_toolbar, text="",
                                           font=FontManager.get("small"))
        self._rcpt_status_lbl.pack(side=tk.LEFT, padx=6, pady=2)
        self._lookup_btn = ttk.Button(rcpt_toolbar, text="Lookup",
                                      command=self._run_lookup_now)
        self._lookup_btn.pack(side=tk.RIGHT, padx=6, pady=2)

        self._rcpt_text = tk.Text(rcpt_frame, height=4, wrap=tk.NONE,
                                  font=FontManager.get("mono"),
                                  bg=_tc("terminal_bg", "#0d1117"),
                                  fg=_tc("terminal_fg", "#c9d1d9"),
                                  relief=tk.FLAT, state=tk.DISABLED)
        rcpt_hsb = ttk.Scrollbar(rcpt_frame, orient=tk.HORIZONTAL,
                                  command=self._rcpt_text.xview)
        self._rcpt_text.configure(xscrollcommand=rcpt_hsb.set)
        for tag, fg in [("ok",       _tc("ok_fg",    "#4caf50")),
                         ("warn",     _tc("warn_fg",  "#ff9800")),
                         ("err",      _tc("err_fg",   "#f44336")),
                         ("email",    _tc("email_fg", "#64b5f6")),
                         ("name",     _tc("name_fg",  "#aaaaaa")),
                         ("label",    _tc("label_fg", "#888888")),
                         ("pending",  _tc("dim_fg",   "#888888"))]:
            self._rcpt_text.tag_configure(tag, foreground=fg)
        rcpt_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._rcpt_text.pack(fill=tk.X, expand=False, padx=4, pady=(0, 4))

        # Internal state for async lookup
        self._lookup_after_id = None
        self._lookup_lock     = threading.Lock()
        self._lookup_results  = {}   # {token: pb_result}

        preview_frame = ttk.LabelFrame(self, text=" Command that will be run ")
        preview_frame.pack(fill=tk.X, expand=False, padx=14, pady=(0, 4))

        cmd_toolbar = ttk.Frame(preview_frame)
        cmd_toolbar.pack(fill=tk.X)
        ttk.Label(cmd_toolbar,
                  text="(copy and run in terminal to test manually)",
                  foreground="#666").pack(side=tk.LEFT, padx=6, pady=2)
        self._copy_cmd_btn = ttk.Button(cmd_toolbar, text="Copy command",
                                        command=self._copy_command)
        self._copy_cmd_btn.pack(side=tk.RIGHT, padx=6, pady=2)

        self._cmd_text = tk.Text(preview_frame, height=3, wrap=tk.NONE,
                                 font=FontManager.get("mono"),
                                 bg=_tc("terminal_bg", "#1a1a2e"),
                                 fg=_tc("terminal_fg", "#69f0ae"),
                                 relief=tk.FLAT, state=tk.DISABLED)
        cmd_vsb = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL,
                                 command=self._cmd_text.yview)
        cmd_hsb = ttk.Scrollbar(preview_frame, orient=tk.HORIZONTAL,
                                 command=self._cmd_text.xview)
        self._cmd_text.configure(yscrollcommand=cmd_vsb.set,
                                  xscrollcommand=cmd_hsb.set)
        cmd_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        cmd_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._cmd_text.pack(fill=tk.X, expand=False, padx=4, pady=(0, 4))

        # Output
        out_frame = ttk.LabelFrame(self, text=" Output after send ")
        out_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 4))

        self._out_text = tk.Text(out_frame, height=6, wrap=tk.NONE,
                                  font=FontManager.get("mono"),
                                  bg=_tc("terminal_bg", "#111111"),
                                  fg=_tc("terminal_fg", "#e0e0e0"),
                                  relief=tk.FLAT, state=tk.DISABLED)
        out_vsb = ttk.Scrollbar(out_frame, orient=tk.VERTICAL,
                                 command=self._out_text.yview)
        out_hsb = ttk.Scrollbar(out_frame, orient=tk.HORIZONTAL,
                                 command=self._out_text.xview)
        self._out_text.configure(yscrollcommand=out_vsb.set,
                                  xscrollcommand=out_hsb.set)
        for tag, fg in [("ok",    _tc("ok_fg",       "#69f0ae")),
                         ("err",   _tc("err_fg",      "#ff5252")),
                         ("dim",   _tc("dim_fg",      "#888888")),
                         ("cmd",   _tc("email_fg",    "#4fc3f7")),
                         ("plain", _tc("terminal_fg", "#e0e0e0"))]:
            self._out_text.tag_configure(tag, foreground=fg)
        out_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        out_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._out_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # Buttons + font size controls
        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, padx=14, pady=(0, 10))
        ttk.Button(btn_row, text="Close", command=self.destroy
                   ).pack(side=tk.RIGHT, padx=4)
        self._send_btn = ttk.Button(btn_row, text="Send",
                                    command=self._on_send)
        self._send_btn.pack(side=tk.RIGHT, padx=4)

        # Font size controls on the left side of button row
        ttk.Label(btn_row, text="Font:").pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(btn_row, text="-", width=2,
                   command=lambda: self._set_font_size(FontManager._size - 1)
                   ).pack(side=tk.LEFT, padx=1)
        self._font_size_var = tk.StringVar(value=str(FontManager._size))
        self._font_size_entry = ttk.Entry(btn_row, textvariable=self._font_size_var,
                                           width=4, justify=tk.CENTER)
        self._font_size_entry.pack(side=tk.LEFT, padx=1)
        self._font_size_entry.bind("<Return>",   self._on_font_size_entry)
        self._font_size_entry.bind("<FocusOut>", self._on_font_size_entry)
        ttk.Button(btn_row, text="+", width=2,
                   command=lambda: self._set_font_size(FontManager._size + 1)
                   ).pack(side=tk.LEFT, padx=1)

        # Keyboard shortcuts Ctrl++ / Ctrl+-
        self.bind_all("<Control-equal>",      lambda e: self._set_font_size(FontManager._size + 1))
        self.bind_all("<Control-plus>",       lambda e: self._set_font_size(FontManager._size + 1))
        self.bind_all("<Control-minus>",      lambda e: self._set_font_size(FontManager._size - 1))
        self.bind_all("<Control-underscore>", lambda e: self._set_font_size(FontManager._size - 1))

        FontManager.add_listener(self._on_font_change)
        if _TM_OK and _TM is not None:
            _TM.add_listener(self._on_theme_change)
        self.bind("<Destroy>", self._on_dialog_destroy)
        self._on_theme_change()         # apply current colors to all tk widgets
        self._on_font_change()          # apply current size to all widgets immediately
        self._on_field_change()
        self._schedule_lookup(delay_ms=100)   # initial lookup on open

        self.update_idletasks()
        w = max(self.winfo_reqwidth(),  660)
        h = max(self.winfo_reqheight(), 520)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self._to_entry.focus_set()
        # In button-click mode (embedded in a running app), block here until
        # the dialog closes.  In CLI mode the caller drives mainloop() itself
        # and we must NOT call wait_window inside __init__ before mainloop starts.
        if self._block:
            self.wait_window(self)

    # ------------------------------------------------------------------
    def _build_preview_cmd(self, attach_paths: "Optional[List[str]]" = None) -> str:
        to_list = _resolve_addr_list(self._to_var.get())
        cc_list = _resolve_addr_list(self._cc_var.get())
        subject = self._subj_var.get().strip() or "Email"

        if attach_paths is not None:
            paths = attach_paths
        elif _is_dataframe(self._attach):
            # DataFrame attachment still pending — show placeholder
            paths = self._attach_paths + ["/tmp/email_sender_XXXXX.csv"]
        else:
            paths = list(self._attach_paths)

        parts = ['echo "<message body>"', "|", "mutt", f'-s "{subject}"']
        for cc in cc_list:
            parts += [f'-c "{cc}"']
        for p in paths:
            parts += ["-a", p]
        if paths:
            parts += ["--"]
        parts += (to_list or ["<recipient>"])
        return " ".join(parts)

    def _on_field_change(self, *_):
        _set_readonly_text(self._cmd_text, self._build_preview_cmd())

    def _schedule_lookup(self, delay_ms: int = 600):
        """Debounce: cancel any pending lookup and reschedule after delay."""
        if self._lookup_after_id is not None:
            try:
                self.after_cancel(self._lookup_after_id)
            except Exception:
                pass
        self._rcpt_status_lbl.configure(text="looking up...", foreground="#888888")
        self._lookup_after_id = self.after(delay_ms, self._run_lookup_now)

    def _run_lookup_now(self):
        """Kick off phonebook lookups for all tokens in a background thread."""
        self._lookup_after_id = None
        to_raw  = self._to_var.get()
        cc_raw  = self._cc_var.get()
        tokens  = []
        for tok in (to_raw + "," + cc_raw).split(","):
            tok = tok.strip()
            if tok:
                tokens.append(tok)
        if not tokens:
            self._render_lookup({})
            return

        self._rcpt_status_lbl.configure(text="looking up...", foreground="#888888")
        self._lookup_btn.configure(state=tk.DISABLED)

        def _worker():
            results = {}
            for tok in tokens:
                results[tok] = _pb_lookup(tok)
            self.after(0, lambda: self._on_lookup_done(results))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_lookup_done(self, results: dict):
        """Called on main thread when all lookups complete."""
        self._lookup_btn.configure(state=tk.NORMAL)
        with self._lookup_lock:
            self._lookup_results = results
        self._render_lookup(results)
        # Rebuild command preview with validated addresses
        self._on_field_change()

    def _render_lookup(self, results: dict):
        """Paint the recipient preview box — one line per recipient."""
        to_raw = self._to_var.get()
        cc_raw = self._cc_var.get()

        to_tokens = [t.strip() for t in to_raw.split(",") if t.strip()]
        cc_tokens = [t.strip() for t in cc_raw.split(",") if t.strip()]

        n_ok = n_warn = n_err = 0
        lines = []   # list of segment-lists; each inner list = one output line

        def _emit(field_label, tokens):
            nonlocal n_ok, n_warn, n_err
            if not tokens:
                return
            label_prefix = f"  {field_label}  "
            indent       = " " * len(label_prefix)

            for i, tok in enumerate(tokens):
                prefix  = label_prefix if i == 0 else indent
                r       = results.get(tok, {})
                status  = r.get("status", "pending")
                email   = r.get("email")
                name    = r.get("name")
                matches = r.get("matches", [])

                if status == "pending":
                    lines.append([(prefix, "label"),
                                  (tok, "pending"),
                                  ("  [looking up...]", "pending")])

                elif status in ("ok", "has_email", "no_pb"):
                    n_ok += 1
                    icon = ("[ok]"    if status == "ok"
                            else "[em]"  if status == "has_email"
                            else "[~]")
                    if name:
                        lines.append([(prefix, "label"),
                                      (f"{icon} {name} ", "name"),
                                      (f"<{email}>", "email")])
                    else:
                        lines.append([(prefix, "label"),
                                      (f"{icon} {email}", "email")])

                elif status == "ambiguous":
                    n_warn += 1
                    lines.append([(prefix, "label"),
                                  (f"[?]  '{tok}' -- ambiguous ({len(matches)} matches):",
                                   "warn")])
                    for mn, me in matches[:4]:
                        lines.append([(indent, "label"),
                                      (f"      {mn} <{me}>", "warn")])
                    if len(matches) > 4:
                        lines.append([(indent, "label"),
                                      (f"      ... +{len(matches)-4} more", "warn")])

                elif status == "not_found":
                    n_err += 1
                    lines.append([(prefix, "label"),
                                  (f"[x]  '{tok}'  not found -- will send to ", "err"),
                                  (email or f"{tok}@{_DOMAIN}", "email")])

        _emit("To:", to_tokens)
        _emit("CC:", cc_tokens)

        # Write into widget
        self._rcpt_text.configure(state=tk.NORMAL)
        self._rcpt_text.delete("1.0", tk.END)
        for row_segs in lines:
            for text, tag in row_segs:
                self._rcpt_text.insert(tk.END, text, tag)
            self._rcpt_text.insert(tk.END, "\n")
        self._rcpt_text.configure(state=tk.DISABLED)

        # Summary label
        total = n_ok + n_warn + n_err
        if total == 0:
            self._rcpt_status_lbl.configure(text="", foreground="#888")
        elif n_err > 0:
            self._rcpt_status_lbl.configure(
                text=f"{n_ok} resolved,  {n_warn} ambiguous,  {n_err} not found",
                foreground="#f44336")
        elif n_warn > 0:
            self._rcpt_status_lbl.configure(
                text=f"{n_ok} resolved,  {n_warn} ambiguous",
                foreground="#ff9800")
        else:
            self._rcpt_status_lbl.configure(
                text=f"All {n_ok} recipient(s) verified",
                foreground="#4caf50")

        # Disable Send if nothing resolved
        self._send_btn.configure(
            state=tk.NORMAL if n_ok > 0 else tk.DISABLED
        )

    # ------------------------------------------------------------------
    # Attachment management
    # ------------------------------------------------------------------

    def _load_body_file(self):
        """Open a file dialog and load its text content into the message body."""
        path = filedialog.askopenfilename(
            title="Load message body from file",
            filetypes=[("Text files", "*.txt *.md *.log *.csv"),
                       ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as exc:
            messagebox.showerror("Error loading file",
                                 f"Could not read:\n{path}\n\n{exc}",
                                 parent=self)
            return
        self._body_text.delete("1.0", tk.END)
        self._body_text.insert("1.0", content)

    def _browse_add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select files to attach",
            parent=self,
        )
        for p in paths:
            if p not in self._attach_paths:
                self._attach_paths.append(p)
                self._attach_lb.insert(tk.END, f"  {os.path.basename(p)}   {p}")
        n = len(self._attach_paths)
        self._attach_count_lbl.configure(
            text=f"{n} attachment(s)" if n else "no attachments")
        self._on_field_change()

    def _remove_selected_attach(self):
        # Delete in reverse order so indices don't shift
        for idx in reversed(self._attach_lb.curselection()):
            self._attach_lb.delete(idx)
            if idx < len(self._attach_paths):
                self._attach_paths.pop(idx)
        n = len(self._attach_paths)
        self._attach_count_lbl.configure(
            text=f"{n} attachment(s)" if n else "no attachments")
        self._on_field_change()

    # ------------------------------------------------------------------
    # Font size controls
    # ------------------------------------------------------------------

    def _set_font_size(self, size: int):
        size = max(7, min(size, 32))
        FontManager._size = size
        # _on_font_change (registered as a FontManager listener) does everything
        self._on_font_change()

    def _on_font_size_entry(self, event=None):
        try:
            size = int(self._font_size_var.get())
        except ValueError:
            self._font_size_var.set(str(FontManager._size))
            return
        self._set_font_size(size)

    def _copy_command(self):
        self.clipboard_clear()
        self.clipboard_append(self._build_preview_cmd())
        self._copy_cmd_btn.configure(text="Copied!")
        self.after(1500, lambda: self._copy_cmd_btn.configure(text="Copy command"))

    def _on_dialog_destroy(self, event=None):
        """Clean up listeners when the dialog window is destroyed."""
        try:
            FontManager.remove_listener(self._on_font_change)
        except Exception:
            pass
        if _TM_OK and _TM is not None:
            try:
                _TM.remove_listener(self._on_theme_change)
            except Exception:
                pass

    def _on_theme_change(self):
        """Re-apply all plain-tk widget colors from the current theme."""
        try:
            hdr_bg  = _tc("hdr_bg",      "#1a237e")
            hdr_fg  = _tc("hdr_fg",      "#ffffff")
            e_bg    = _tc("entry_bg",     "#fafafa")
            e_fg    = _tc("entry_fg",     "#222222")
            sel_bg  = _tc("sel_bg",       "#1565c0")
            sel_fg  = _tc("sel_fg",       "#ffffff")
            t_bg    = _tc("terminal_bg",  "#0d1117")
            t_fg    = _tc("terminal_fg",  "#c9d1d9")

            # Toplevel + header
            self._hdr_frame.configure(bg=hdr_bg)
            self._hdr_label.configure(bg=hdr_bg, fg=hdr_fg)

            # Attach listbox — update bg/fg + border highlight
            self._attach_lb.configure(
                bg=e_bg, fg=e_fg,
                selectbackground=sel_bg, selectforeground=sel_fg,
                highlightbackground=sel_bg, highlightcolor=sel_bg,
            )

            # Text widgets
            self._body_text.configure(bg=e_bg, fg=e_fg)
            self._rcpt_text.configure(bg=t_bg, fg=t_fg)
            self._cmd_text.configure(bg=t_bg, fg=t_fg)
            self._out_text.configure(bg=t_bg, fg=t_fg)

            # Re-apply option_add defaults so any newly created plain-tk
            # children also pick up the new theme.
            if _TM_OK and _TM is not None:
                _TM.apply_to_root(self)

        except tk.TclError:
            pass

    def _on_font_change(self):
        try:
            s = FontManager._size
            normal = FontManager.get("normal")   # ("TkDefaultFont", s)
            mono   = FontManager.get("mono")     # ("Courier", s)
            bold   = FontManager.get("bold")     # ("TkDefaultFont", s, "bold")
            small  = FontManager.get("small")    # ("TkDefaultFont", s-1)

            # --- ttk Style: covers ALL ttk widgets (Label, Button, Entry,
            #     LabelFrame, Checkbutton, Combobox, …) in one shot ----------
            style = ttk.Style(self)
            for widget_class in ("TLabel", "TButton", "TEntry", "TLabelframe",
                                 "TLabelframe.Label", "TCheckbutton",
                                 "TCombobox", "TRadiobutton"):
                style.configure(widget_class, font=normal)

            # --- header tk.Label (not a ttk widget) -------------------------
            self._hdr_label.configure(font=bold)

            # --- text boxes (tk.Text) ---------------------------------------
            for w in (self._body_text, self._cmd_text, self._out_text,
                      self._rcpt_text):
                w.configure(font=mono)

            # --- attachment listbox -----------------------------------------
            self._attach_lb.configure(font=mono)

            # --- font-size entry stays in sync ------------------------------
            self._font_size_var.set(str(s))

        except tk.TclError:
            pass

    def _append_out(self, text: str, tag: str = "plain"):
        self._out_text.configure(state=tk.NORMAL)
        self._out_text.insert(tk.END, text, tag)
        self._out_text.configure(state=tk.DISABLED)
        self._out_text.see(tk.END)

    def _clear_out(self):
        self._out_text.configure(state=tk.NORMAL)
        self._out_text.delete("1.0", tk.END)
        self._out_text.configure(state=tk.DISABLED)

    def _resolved_list(self, raw: str) -> List[str]:
        """
        Return list of resolved email addresses for ``raw`` using cached
        lookup results when available, falling back to direct resolution.
        """
        parts = [t.strip() for t in raw.split(",") if t.strip()]
        result = []
        for tok in parts:
            r = self._lookup_results.get(tok) or _pb_lookup(tok)
            if r.get("email"):
                result.append(r["email"])
        return result

    def _on_send(self):
        # Use already-resolved addresses from lookup cache
        to_raw = self._to_var.get()
        cc_raw = self._cc_var.get()
        to_list = self._resolved_list(to_raw)
        cc_list = self._resolved_list(cc_raw)

        if not to_list:
            messagebox.showerror(
                "Missing recipient",
                "Please enter an email ID or address.\n"
                "Multiple recipients can be comma-separated.\n\n"
                "Use the Lookup button to validate before sending.",
                parent=self,
            )
            return
        subject = self._subj_var.get().strip() or "Email"
        body    = self._body_text.get("1.0", tk.END).strip()
        # cc_list already resolved above

        self._send_btn.configure(state=tk.DISABLED, text="Sending...")
        self._clear_out()
        self.update_idletasks()

        # Build the effective attach argument: DataFrame + any extra paths
        if _is_dataframe(self._attach):
            # Pass DataFrame; extra paths (if any) added by _send_email separately
            send_attach = self._attach
            extra_paths = list(self._attach_paths)
        else:
            send_attach = self._attach_paths if self._attach_paths else None
            extra_paths = []

        ok, cmd_used, stdout_txt, stderr_txt, retcode = \
            _send_email(send_attach, to_list, subject, body, cc_list,
                        extra_paths=extra_paths)

        self._send_btn.configure(state=tk.NORMAL, text="Send")

        self._append_out("$ ", "dim")
        self._append_out(" ".join(cmd_used) + "\n", "cmd")
        self._append_out(f"Exit code: {retcode}\n",
                         "ok" if retcode == 0 else "err")
        if stdout_txt.strip():
            self._append_out("-- stdout --\n", "dim")
            self._append_out(stdout_txt + "\n", "plain")
        if stderr_txt.strip():
            self._append_out("-- stderr --\n", "dim")
            self._append_out(stderr_txt + "\n", "err")

        if ok:
            self._append_out(
                f"\n  Mail accepted -> {', '.join(to_list)}\n", "ok")
        else:
            self._append_out(
                "\n  Send failed -- copy the command above and run it in a "
                "terminal to debug.\n", "err")

        if cmd_used:
            _set_readonly_text(self._cmd_text, " ".join(cmd_used))


# =============================================================================
# Low-level send (no UI; fully testable in isolation)
# =============================================================================

def _send_email(attach: "_AttachInput", to_list: List[str], subject: str,
                body: str, cc_list: "Optional[List[str]]" = None,
                extra_paths: "Optional[List[str]]" = None) -> tuple:
    """
    Resolve attachments, send via mutt then mailx fallback.

    DataFrame attachments are written to a temp CSV and cleaned up afterwards.
    ``extra_paths`` is an optional list of additional file paths to attach
    (e.g. paths added interactively in the GUI beyond the original attach arg).

    Returns
    -------
    (success: bool, cmd: list, stdout: str, stderr: str, returncode: int)
    """
    cc_list    = cc_list or []
    extra_paths = extra_paths or []
    _tmpfiles: List[str] = []

    try:
        # ---- resolve attach -> list[str] ------------------------------------
        if attach is None:
            paths: List[str] = []
        elif _is_dataframe(attach):
            fd, tmp = tempfile.mkstemp(suffix=".csv", prefix="email_sender_")
            os.close(fd)
            attach.to_csv(tmp, index=False)  # type: ignore[union-attr]
            _tmpfiles.append(tmp)
            paths = [tmp]
        elif isinstance(attach, str):
            paths = [attach]
        elif isinstance(attach, list):
            paths = []
            for item in attach:
                if _is_dataframe(item):
                    fd, tmp = tempfile.mkstemp(suffix=".csv", prefix="email_sender_")
                    os.close(fd)
                    item.to_csv(tmp, index=False)  # type: ignore[union-attr]
                    _tmpfiles.append(tmp)
                    paths.append(tmp)
                else:
                    paths.append(str(item))
        else:
            paths = []

        # Append any extra paths added interactively
        paths += [p for p in extra_paths if p not in paths]

        def _run(cmd: list) -> tuple:
            try:
                r = subprocess.run(cmd, input=body or " ", text=True,
                                   capture_output=True, timeout=30)
                return r.returncode == 0, r.stdout, r.stderr, r.returncode
            except subprocess.TimeoutExpired:
                return False, "", "Command timed out after 30 s.", -1
            except FileNotFoundError:
                return False, "", f"{cmd[0]}: command not found", 127

        def _build_cmd(binary: str) -> list:
            cmd = [binary, "-s", subject]
            for cc in cc_list:
                cmd += ["-c", cc]
            for p in paths:
                cmd += ["-a", p]
            if paths:
                cmd += ["--"]
            cmd += to_list
            return cmd

        # attempt 1: mutt
        mutt = subprocess.run(["which", "mutt"],
                               capture_output=True, text=True).stdout.strip()
        if mutt:
            cmd1 = _build_cmd(mutt)
            ok1, out1, err1, rc1 = _run(cmd1)
            if ok1:
                return True, cmd1, out1, err1, rc1
            mutt_result = (cmd1, out1, err1, rc1)
        else:
            mutt_result = (["mutt"], "", "mutt not in PATH", 127)

        # attempt 2: mailx
        mailx = subprocess.run(["which", "mailx"],
                                capture_output=True, text=True).stdout.strip()
        if mailx:
            cmd2 = _build_cmd(mailx)
            ok2, out2, err2, rc2 = _run(cmd2)
            if ok2:
                return True, cmd2, out2, err2, rc2
            combined_err = (
                f"mutt exit={mutt_result[3]}: "
                f"{(mutt_result[2] or mutt_result[1]).strip()}\n"
                f"mailx exit={rc2}: {(err2 or out2).strip()}"
            )
            return False, mutt_result[0], mutt_result[1], combined_err, mutt_result[3]

        return False, mutt_result[0], mutt_result[1], mutt_result[2], mutt_result[3]

    finally:
        for tmp in _tmpfiles:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# =============================================================================
# Convenience API
# =============================================================================

def send_email_with_attachment(parent,
                               attach: "_AttachInput" = None,
                               default_subject: str = "",
                               default_body: str = "",
                               df=None):
    """
    Open the EmailDialog.

    Parameters
    ----------
    parent          : tk widget
    attach          : str | list[str] | pd.DataFrame | None
    default_subject : pre-filled subject line
    default_body    : pre-filled message body text
    df              : DEPRECATED alias for ``attach``
    """
    if attach is None and df is not None:
        attach = df
    EmailDialog(parent, attach=attach, default_subject=default_subject,
                default_body=default_body)


def make_email_button(toolbar,
                      get_attach: "Optional[Callable]" = None,
                      get_subject: "Optional[Callable]" = None,
                      empty_msg: str = "Nothing to email.",
                      get_df: "Optional[Callable]" = None,
                      **btn_kwargs) -> ttk.Button:
    """
    Create a ``Email...`` button and return it (not packed).

    Parameters
    ----------
    toolbar     : parent tk/ttk widget
    get_attach  : callable() -> str | list[str] | pd.DataFrame | None
                  Evaluated on each click.  Any file type is accepted.
    get_subject : callable() -> str, or None
    empty_msg   : warning when result is None or empty DataFrame
    get_df      : DEPRECATED alias for ``get_attach``
    **btn_kwargs: forwarded to ttk.Button

    Returns an unpacked ttk.Button.

    Examples
    --------
    ::
        # Any file
        btn = make_email_button(toolbar,
                                get_attach=lambda: "/tmp/report.pdf")

        # Multiple files
        btn = make_email_button(toolbar,
                                get_attach=lambda: ["/tmp/a.csv", "/tmp/b.log"])

        # DataFrame (backward compat)
        btn = make_email_button(toolbar, get_df=lambda: table.current_df())

        btn.pack(side=tk.RIGHT, padx=4)
    """
    if get_attach is None and get_df is not None:
        get_attach = get_df

    btn_kwargs.setdefault("text", "Email...")

    def _on_click():
        a = get_attach() if get_attach is not None else None
        if a is None:
            messagebox.showwarning("Nothing to email", empty_msg,
                                   parent=toolbar)
            return
        if _is_dataframe(a) and len(a) == 0:
            messagebox.showwarning("Nothing to email", empty_msg,
                                   parent=toolbar)
            return
        subject = get_subject() if get_subject is not None else ""
        send_email_with_attachment(toolbar, attach=a, default_subject=subject)

    return ttk.Button(toolbar, command=_on_click, **btn_kwargs)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli():
    """Command-line interface: open the email dialog without a parent app."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="email_sender.py",
        description="Send an email with optional file attachments via a GUI dialog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # No attachment (body-only):
  python3 email_sender.py -s "Hello"

  # Pre-fill body from a file:
  python3 email_sender.py -b message.txt -s "Weekly report"

  # Single file attachment:
  python3 email_sender.py report.pdf

  # Multiple files:
  python3 email_sender.py a.csv b.log c.pdf

  # Override default subject:
  python3 email_sender.py -s "Weekly report" data.csv

  # Pre-fill recipients:
  python3 email_sender.py -t alice,bob -s "FYI" notes.txt
""",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="File(s) to attach.  If omitted the dialog opens in body-only mode.",
    )
    parser.add_argument(
        "-b", "--body-file",
        default="",
        metavar="FILE",
        help="Path to a text file whose contents are pre-filled as the message body.",
    )
    parser.add_argument(
        "-s", "--subject",
        default="",
        metavar="SUBJECT",
        help="Default subject line pre-filled in the dialog.",
    )
    parser.add_argument(
        "-t", "--to",
        default="",
        metavar="EMAILS",
        help="Comma-separated recipient email-ids (intel.com appended if no @).",
    )
    args = parser.parse_args()

    attach = args.files if args.files else None
    if attach and len(attach) == 1:
        attach = attach[0]           # single file → str (not list)

    # Read body file if supplied on the command line
    default_body = ""
    if args.body_file:
        try:
            with open(args.body_file, "r", encoding="utf-8", errors="replace") as _fh:
                default_body = _fh.read()
        except Exception as _exc:
            import sys
            print(f"Warning: could not read --body-file: {_exc}", file=sys.stderr)

    import tkinter as tk

    root = tk.Tk()
    # Keep root alive but invisible.
    # NOTE: root.withdraw() suppresses transient Toplevels on Linux/X11 —
    # so we position root off-screen at 1×1 px instead.
    root.geometry("1x1+-9999+-9999")
    root.overrideredirect(True)      # no title bar on the dummy root
    root.attributes("-alpha", 0.0)   # fully transparent (belt-and-suspenders)

    def _open_dialog():
        dlg = EmailDialog(root, attach=attach, default_subject=args.subject,
                          default_body=default_body,
                          _block=False)   # mainloop drives events, not wait_window
        # Pre-fill To field if supplied on command line
        if args.to:
            dlg._to_var.set(args.to)
        # When the dialog closes, quit the event loop
        root.wait_window(dlg)
        root.quit()

    root.after(0, _open_dialog)      # schedule after mainloop starts
    root.mainloop()
    root.destroy()


if __name__ == "__main__":
    _cli()
