# email_sender — Reusable Email Widget (any file attachment)

A self-contained tkinter widget that sends email with any file attached —
CSV, PDF, log, or any other type.  Can be dropped into any tool in two lines.

## Quick start

```python
import sys
sys.path.insert(0, "/path/to/scripts/email")
from email_sender import make_email_button, send_email_with_attachment
```

## Attachment types

| `attach=` value | What happens |
|-----------------|--------------|
| `"/tmp/report.pdf"` | Attaches that file directly |
| `["/tmp/a.csv", "/tmp/b.log"]` | Attaches both files |
| `my_df` (pd.DataFrame) | Written to a temp CSV just before sending |
| `None` (default) | Body-only, no attachment |

## Usage examples

### Any file

```python
send_email_with_attachment(parent, attach="/tmp/report.pdf",
                           default_subject="Weekly Report")
```

### Multiple files

```python
send_email_with_attachment(parent,
                           attach=["/tmp/results.csv", "/tmp/run.log"],
                           default_subject="Pipeline results")
```

### DataFrame (temp CSV)

```python
send_email_with_attachment(parent, attach=df, default_subject="Filtered view")
```

### Body-only

```python
send_email_with_attachment(parent, default_subject="Hello")
```

### Toolbar button — any file from a callable

```python
btn = make_email_button(
    toolbar,
    get_attach  = lambda: my_tool.get_output_path(),   # any file
    get_subject = lambda: "Results",
)
btn.pack(side=tk.RIGHT, padx=4)
```

### Toolbar button — DataFrame (FilteredTable style)

```python
btn = make_email_button(
    toolbar,
    get_df      = lambda: my_table.current_df(),   # backward-compat alias
)
btn.pack(side=tk.RIGHT, padx=4)
```

### Direct class use

```python
from email_sender import EmailDialog
EmailDialog(parent, attach="/tmp/foo.txt", default_subject="Ad-hoc")
```

## Features

| Feature | Detail |
|---------|--------|
| **Any file type** | Attach any file by path; attach multiple files as a list |
| **DataFrame support** | Pass a `pd.DataFrame`; written to temp CSV on send |
| **Body-only** | Pass `attach=None` to send just a message body |
| **Pre-filled body** | Pass `default_body="..."` or `--body-file FILE` to pre-populate the message body |
| **Bare-ID expansion** | `nsaddaga` → `nsaddaga@intel.com`; comma-separated To and CC |
| **Live command preview** | Shows exact `mutt`/`mailx` shell command before sending |
| **Copy command** | One-click clipboard copy for terminal debugging |
| **mutt → mailx fallback** | Tries `mutt` first; falls back to `mailx` automatically |
| **Stdout/stderr output** | Full command output shown after send attempt |
| **Font-aware** | Respects `FontManager` if available; minimal fallback otherwise |
| **pandas optional** | Module loads fine without pandas when no DataFrame is passed |

## API reference

### `EmailDialog(parent, attach=None, df=None, default_subject="", default_body="", **kwargs)`

Modal `tk.Toplevel`. Blocks until closed.

- `attach` — `str | list[str] | pd.DataFrame | None`
- `df` — DEPRECATED alias for `attach` (backward compat with FilteredTable)
- `default_body` — string pre-filled into the message body; also loadable at
  runtime via the **"Load file…"** button next to the Message label

### `send_email_with_attachment(parent, attach=None, default_subject="", default_body="", df=None)`

Opens `EmailDialog`. One-liner wrapper.

### `make_email_button(toolbar, get_attach=None, get_subject=None, empty_msg=..., get_df=None, **btn_kwargs) → ttk.Button`

Returns an **unpacked** `ttk.Button`. Call `.pack()` / `.grid()` yourself.

- `get_attach` — `callable() → str | list[str] | pd.DataFrame | None`
- `get_subject` — `callable() → str` or `None`
- `get_df` — DEPRECATED alias for `get_attach`
- `**btn_kwargs` — forwarded to `ttk.Button` (default `text="Email..."`)

### `_send_email(attach, to_list, subject, body, cc_list=None) → tuple`

Headless send function — no UI, fully testable.
Returns `(success, cmd, stdout, stderr, returncode)`.

## CLI usage

The module is also executable as a standalone command:

```tcsh
python3 scripts/email/email_sender.py [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-t`, `--to TO` | Recipient(s), comma-separated |
| `-s`, `--subject SUBJECT` | Email subject |
| `-b`, `--body-file FILE` | Read message body from FILE (pre-fills the body field in the GUI) |
| `-a`, `--attach FILE [FILE …]` | File(s) to attach |

```tcsh
# Pre-fill body from a text file
python3 scripts/email/email_sender.py \
    -t nsaddaga \
    -s "Nightly report" \
    -b /tmp/report_notes.txt \
    -a /tmp/results.csv
```

The GUI opens pre-populated; the user can edit before sending.
The **"Load file…"** button in the Message row lets you load a body file
interactively inside the dialog too.

## Recipient format

| Input | Resolved to |
|-------|-------------|
| `nsaddaga` | `nsaddaga@intel.com` |
| `nsaddaga, user2` | `nsaddaga@intel.com`, `user2@intel.com` |
| `full@other.com` | `full@other.com` (unchanged) |
| `nsaddaga, full@other.com` | both expanded appropriately |

## Dependencies

- Python 3.8+, `tkinter`, `subprocess` (stdlib — always available)
- `pandas` — **optional**; only needed when passing a DataFrame
- `mutt` or `mailx` on PATH (Intel Linux systems)
- `scripts/supercsv/font_manager.py` — **optional**; minimal fallback used automatically

## Important: do not add `scripts/` to sys.path

`scripts/email/` being a directory would shadow the Python stdlib `email`
package if `scripts/` were in sys.path.  Always add `scripts/email/` directly:

```python
sys.path.insert(0, "/path/to/scripts/email")   # correct
# NOT: sys.path.insert(0, "/path/to/scripts")   # wrong — shadows stdlib email
```

## Integration with FilteredTable

`scripts/supercsv/filtered_table.py` imports `EmailDialog` and
`send_email_with_attachment` from here.  The `Email…` button in the
FilteredTable toolbar calls:

```python
send_email_with_attachment(self, attach=self._df_view.copy(), ...)
```

No changes to `filtered_table.py` are needed when modifying email behaviour —
edit `email_sender.py` only.
