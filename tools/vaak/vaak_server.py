#!/usr/bin/env python3
"""Vaak — a Win+H-friendly web box that types into a tmux-hosted CLI session.

Windows Voice Typing (Win+H) refuses to dictate into terminal emulators
("this app might not support voice features"), but it works perfectly into a
browser text field. This tiny server serves such a field and injects whatever
you send into a target tmux pane via `tmux send-keys`, so you can dictate on
your laptop and have the words appear at the Copilot CLI prompt.

Workflow
--------
1. Run the CLI inside tmux so this bridge can reach it:

       tmux new -s copilot
       clic                       # (your Copilot CLI alias), inside that tmux

2. In another shell, start this bridge pointed at that tmux target:

       VAAK_TARGET=copilot python3 vaak_server.py

3. Open the printed URL (it carries a one-time token) in your browser,
   click the box, press Win+H, speak, and hit Send.

Environment
-----------
  VAAK_PORT     HTTP port (default 8781).
  VAAK_TARGET   tmux target: session, window, or pane (default "copilot").
                   Examples: "copilot", "copilot:0", "copilot:0.0".
  VAAK_TOKEN    Shared secret required on send (default: random per start).
  VAAK_HOST     Bind address (default 0.0.0.0 so a laptop browser can reach
                   this host by IP on the internal network).
  VAAK_PANE_LINES Default number of tmux pane lines shown by the mirror (40).
  VAAK_SEND_SETTLE Seconds to pause after a queued submit before considering
                   the next queued item (default 0.35).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("VAAK_PORT", "8781"))
HOST = os.environ.get("VAAK_HOST", "0.0.0.0")
# Default target: an explicit VAAK_TARGET wins; otherwise the tmux pane this
# server was launched from ($TMUX_PANE), so `vaak` "just works" from inside
# the session you want to talk to. Falls back to "copilot" for a named session.
TARGET = (os.environ.get("VAAK_TARGET")
          or os.environ.get("TMUX_PANE")
          or "copilot")
TOKEN = os.environ.get("VAAK_TOKEN") or secrets.token_urlsafe(9)

# Busy/ready detection. A session is "busy" when the tail of its pane matches
# this regex. The default targets the GitHub Copilot CLI footer, which shows
# "esc interrupt" / "◉ Working" / "Thinking" only while it is processing.
# Override for other CLIs via VAAK_BUSY_RE.
BUSY_RE = re.compile(
    os.environ.get(
        "VAAK_BUSY_RE",
        r"esc (to )?interrupt|(●|◉|◐|◓|◑|◒|◔|◕|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏)\s*"
        r"(Working|Thinking|Generating|Compacting|Running|Booting)"
        r"|\bThinking\b|\bGenerating\b|\bCompacting\b"),
    re.IGNORECASE)
# How many trailing non-blank pane lines to scan for the busy marker.
STATUS_TAIL = int(os.environ.get("VAAK_STATUS_TAIL", "8"))
PANE_LINES_MAX = 5000
try:
    PANE_LINES_DEFAULT = int(os.environ.get("VAAK_PANE_LINES", "1000"))
except ValueError:
    PANE_LINES_DEFAULT = 1000
PANE_LINES_DEFAULT = max(1, min(PANE_LINES_MAX, PANE_LINES_DEFAULT))
# Where per-session drafts persist (keyed by tmux session name).
DRAFTS_PATH = Path(os.environ.get("VAAK_DRAFTS",
                                  str(Path.home() / ".vaak" / "drafts.json")))
# Where per-session auto-flush enablement persists (list of session names).
AUTOFLUSH_PATH = Path(os.environ.get("VAAK_AUTOFLUSH_PATH",
                                     str(Path.home() / ".vaak" / "autoflush.json")))
# Seconds between auto-flush poller ticks.
AUTOFLUSH_INTERVAL = float(os.environ.get("VAAK_AUTOFLUSH_INTERVAL", "2.0"))
# Grace delay after a session goes ready before auto-sending the next draft.
AUTOFLUSH_GRACE = float(os.environ.get("VAAK_AUTOFLUSH_GRACE", "0.8"))
# Queue-send line discipline. Batched queue senders wait for the target CLI to
# acknowledge each submitted prompt (busy marker or pane change) before they
# consider sending another, so two drafts never land on one input line.
SEND_SETTLE = float(os.environ.get("VAAK_SEND_SETTLE", "0.35"))
SEND_ACCEPT_TIMEOUT = float(os.environ.get("VAAK_SEND_ACCEPT_TIMEOUT", "4.0"))
SEND_READY_TIMEOUT = float(os.environ.get("VAAK_SEND_READY_TIMEOUT", "300.0"))
SEND_POLL_INTERVAL = float(os.environ.get("VAAK_SEND_POLL_INTERVAL", "0.15"))


def _host_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def target_exists(target: str) -> bool:
    r = subprocess.run(["tmux", "list-panes", "-t", target],
                       capture_output=True, text=True, check=False)
    return r.returncode == 0


def resolve_pane_id(target: str) -> str:
    """Resolve a tmux target (session / addr / %id) to its stable pane id."""
    r = subprocess.run(["tmux", "display-message", "-p", "-t", target,
                        "#{pane_id}"], capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def list_targets() -> list[dict]:
    """All tmux panes as {id, label} where id is a stable pane id (%N) and
    label shows session:win.pane plus the command running there."""
    fmt = ("#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}"
           "\t#{pane_current_command}\t#{window_name}")
    r = subprocess.run(["tmux", "list-panes", "-a", "-F", fmt],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return []
    out = []
    for ln in r.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) < 4:
            continue
        pid, addr, cmd, win = parts
        out.append({"id": pid, "label": f"{addr}  [{cmd}] {win}".rstrip()})
    return out


def session_status(target: str) -> str:
    """Return 'busy', 'ready', or 'gone' for a tmux target by scanning the tail
    of its captured pane for the busy marker (see BUSY_RE)."""
    r = subprocess.run(["tmux", "capture-pane", "-p", "-t", target],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return "gone"
    tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-STATUS_TAIL:]
    return "busy" if BUSY_RE.search("\n".join(tail)) else "ready"


def _bounded_pane_lines(value: str | int | None) -> int:
    try:
        n = int(value) if value is not None else PANE_LINES_DEFAULT
    except (TypeError, ValueError):
        n = PANE_LINES_DEFAULT
    return max(1, min(PANE_LINES_MAX, n))


def capture_pane_lines(
    target: str, lines: str | int | None = None
) -> tuple[bool, list[str] | str]:
    """Return the last bounded N captured lines for a tmux target."""
    n = _bounded_pane_lines(lines)
    r = subprocess.run(["tmux", "capture-pane", "-p", "-S", f"-{n}", "-t", target],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, (r.stderr or "capture-pane failed").strip()
    out = r.stdout.splitlines()
    while out and not out[-1].strip():
        out.pop()
    return True, out[-n:]


def list_sessions() -> list[dict]:
    """One entry per tmux *session* (the unit the nav bar and draft queues are
    keyed on): {name, windows, command, status, panes}. `command` is the active
    pane's current command; `status` is busy/ready via session_status."""
    fmt = ("#{session_name}\t#{session_windows}\t#{session_attached}"
           "\t#{pane_current_command}")
    r = subprocess.run(
        ["tmux", "list-sessions", "-F", fmt] if False else
        ["tmux", "list-panes", "-a", "-F",
         "#{session_name}\t#{window_active}\t#{pane_active}\t#{pane_current_command}"],
        capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return []
    # Pick the active pane's command per session.
    cmd_by_sess: dict[str, str] = {}
    seen: list[str] = []
    for ln in r.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) < 4:
            continue
        name, win_active, pane_active, cmd = parts
        if name not in seen:
            seen.append(name)
        if win_active == "1" and pane_active == "1":
            cmd_by_sess[name] = cmd
    out = []
    for name in seen:
        out.append({
            "name": name,
            "command": cmd_by_sess.get(name, ""),
            "status": session_status(name),
        })
    return out


# --- Draft queue persistence (keyed by tmux session name) -------------------
# Session names are stable across restarts (pane ids are not), so drafts survive
# a Vaak restart and re-associate with the same session.
_drafts_lock = threading.RLock()
_autoflush: set[str] = set()   # session names with auto-flush enabled


def _load_autoflush() -> set[str]:
    """Load the persisted auto-flush set from disk. Best-effort — a missing or
    malformed file just yields an empty set."""
    try:
        with AUTOFLUSH_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(x) for x in data if isinstance(x, str) and x}
        if isinstance(data, dict) and isinstance(data.get("sessions"), list):
            return {str(x) for x in data["sessions"] if isinstance(x, str) and x}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _save_autoflush() -> None:
    """Atomically persist the current auto-flush set. Held under _drafts_lock
    (the same lock used for the drafts store) by callers that mutate the set."""
    try:
        AUTOFLUSH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTOFLUSH_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(sorted(_autoflush), f, indent=2)
        tmp.replace(AUTOFLUSH_PATH)
    except OSError:
        pass


# Restore the auto-flush enablement from disk so a Vaak restart doesn't clear
# the user's "Auto-send when ready" toggles.
_autoflush.update(_load_autoflush())


def _load_drafts() -> dict:
    try:
        with DRAFTS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_drafts(data: dict) -> None:
    try:
        DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = DRAFTS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(DRAFTS_PATH)
    except OSError:
        pass


def get_drafts(session: str) -> list[dict]:
    with _drafts_lock:
        return list(_load_drafts().get(session, []))


def add_draft(session: str, text: str) -> dict:
    item = {"id": secrets.token_hex(6), "text": text, "ts": int(time.time())}
    with _drafts_lock:
        data = _load_drafts()
        data.setdefault(session, []).append(item)
        _save_drafts(data)
    return item


def update_draft(session: str, draft_id: str, text: str) -> bool:
    with _drafts_lock:
        data = _load_drafts()
        for it in data.get(session, []):
            if it["id"] == draft_id:
                it["text"] = text
                _save_drafts(data)
                return True
    return False


def delete_draft(session: str, draft_id: str) -> bool:
    with _drafts_lock:
        data = _load_drafts()
        items = data.get(session, [])
        new = [it for it in items if it["id"] != draft_id]
        if len(new) == len(items):
            return False
        data[session] = new
        _save_drafts(data)
    return True


def move_draft(session: str, draft_id: str, direction: str) -> bool:
    delta = -1 if direction == "up" else 1
    with _drafts_lock:
        data = _load_drafts()
        items = data.get(session, [])
        idx = next((i for i, it in enumerate(items) if it["id"] == draft_id), -1)
        if idx < 0:
            return False
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(items):
            return False
        items[idx], items[new_idx] = items[new_idx], items[idx]
        _save_drafts(data)
    return True


def pop_first_draft(session: str) -> dict | None:
    with _drafts_lock:
        data = _load_drafts()
        items = data.get(session, [])
        if not items:
            return None
        first = items.pop(0)
        _save_drafts(data)
    return first


def send_draft(session: str, draft_id: str, submit: bool) -> tuple[bool, str]:
    """Send one specific queued draft (by id) and remove it from the queue on
    success. Refuses if the session is currently busy."""
    with _drafts_lock:
        data = _load_drafts()
        items = data.get(session, [])
        item = next((it for it in items if it["id"] == draft_id), None)
        if item is None:
            return False, "draft not found"
    st = session_status(session)
    if st == "gone":
        return False, f"session '{session}' not found"
    if st == "busy":
        return False, "session is busy"
    ok, info = inject(session, item["text"], submit)
    if ok:
        delete_draft(session, draft_id)
    return ok, info


def _flatten_prompt(text: str) -> str:
    """Collapse a multi-line dictation into a single prompt line: every line
    break (LF, CRLF, or CR) becomes one space so one spoken utterance doesn't
    submit early in a REPL/CLI, and surrounding whitespace is trimmed."""
    return " ".join(text.splitlines()).strip()


def inject(target: str, text: str, submit: bool) -> tuple[bool, str]:
    """Type `text` literally into the tmux target, then optionally press Enter.

    Newlines are flattened to spaces so a single dictated utterance stays one
    prompt (a raw newline would submit early in most REPLs/CLIs)."""
    if not text.strip():
        return False, "empty text"
    if not target_exists(target):
        found = list_targets()
        hint = (" Available: " + ", ".join(t["label"] for t in found)) if found \
            else " No tmux panes found — is the CLI running inside tmux?"
        return False, f"tmux target '{target}' not found.{hint}"
    flat = _flatten_prompt(text)
    # `-l` = literal (no key-name interpretation); `--` = end of options.
    r = subprocess.run(["tmux", "send-keys", "-t", target, "-l", "--", flat],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, (r.stderr or "send-keys failed").strip()
    if submit:
        r2 = subprocess.run(["tmux", "send-keys", "-t", target, "Enter"],
                            capture_output=True, text=True, check=False)
        if r2.returncode != 0:
            return False, (r2.stderr or "Enter failed").strip()
    return True, flat


def _clear_input_line(target: str) -> tuple[bool, str]:
    r = subprocess.run(["tmux", "send-keys", "-t", target, "C-u"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, (r.stderr or "C-u failed").strip()
    return True, ""


def inject_queued(target: str, text: str, submit: bool) -> tuple[bool, str]:
    """Inject one queued draft with input-line cleanup before typing."""
    ok, info = _clear_input_line(target)
    if not ok:
        return False, info
    if SEND_SETTLE > 0:
        time.sleep(min(SEND_SETTLE, 1.0))
    return inject(target, text, submit)


def _wait_for_submission_acceptance(
    target: str, before_lines: list[str] | None, require_busy: bool
) -> tuple[bool, str, bool]:
    """Wait until a submitted queue item is visibly accepted.

    Copilot-like TUIs must flip to "busy"; plain shells may stay "ready" but
    echo/output the submitted line. If the expected signal is not observed,
    callers must not immediately fire the next queued draft.
    """
    if SEND_SETTLE > 0:
        time.sleep(SEND_SETTLE)
    deadline = time.monotonic() + SEND_ACCEPT_TIMEOUT
    last_err = ""
    while time.monotonic() < deadline:
        st = session_status(target)
        if st == "gone":
            return False, "session gone", False
        if st == "busy":
            return True, "session accepted prompt and is busy", True
        ok, lines = capture_pane_lines(target, PANE_LINES_DEFAULT)
        if ok:
            if not require_busy and before_lines is not None and lines != before_lines:
                return True, "pane changed after submit", False
        else:
            last_err = str(lines)
        time.sleep(SEND_POLL_INTERVAL)
    msg = f"no busy marker or pane change within {SEND_ACCEPT_TIMEOUT:g}s"
    if last_err:
        msg += f" ({last_err})"
    return False, msg, False


def _wait_until_ready(target: str) -> tuple[bool, str]:
    deadline = time.monotonic() + SEND_READY_TIMEOUT
    while time.monotonic() < deadline:
        st = session_status(target)
        if st == "ready":
            return True, "ready"
        if st == "gone":
            return False, "session gone"
        time.sleep(SEND_POLL_INTERVAL)
    return False, f"session did not become ready within {SEND_READY_TIMEOUT:g}s"


def _pane_command(target: str) -> str:
    r = subprocess.run(["tmux", "display-message", "-p", "-t", target,
                        "#{pane_current_command}"],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _requires_busy_ack(target: str) -> bool:
    shell_cmds = {"sh", "bash", "zsh", "csh", "tcsh", "ksh", "fish"}
    cmd = _pane_command(target).rsplit("/", 1)[-1]
    return bool(cmd) and cmd not in shell_cmds


def send_queued_item(
    session: str, item: dict, submit: bool
) -> tuple[bool, str, bool, bool]:
    ok, before = capture_pane_lines(session, PANE_LINES_DEFAULT)
    before_lines = before if ok and isinstance(before, list) else None
    ok, info = inject_queued(session, item["text"], submit)
    if not ok:
        return False, info, False, False
    if not submit:
        if SEND_SETTLE > 0:
            time.sleep(SEND_SETTLE)
        return True, info, False, True
    require_busy = _requires_busy_ack(session)
    accepted, reason, saw_busy = _wait_for_submission_acceptance(
        session, before_lines, require_busy)
    if not accepted:
        return True, reason, saw_busy, False
    return True, info, saw_busy, True


def send_all_drafts(session: str, submit: bool) -> tuple[int, list[str]]:
    sent, errors = 0, []
    while True:
        items = get_drafts(session)
        if not items:
            break
        st = session_status(session)
        if st != "ready":
            errors.append(f"stopped: session is {st}")
            break
        item = items[0]
        sent_ok, info, saw_busy, accepted = send_queued_item(session, item, submit)
        if sent_ok:
            delete_draft(session, item["id"])
            sent += 1
        if not sent_ok:
            errors.append(info)
            break
        if not accepted:
            errors.append(info)
            break
        if saw_busy:
            ready, msg = _wait_until_ready(session)
            if not ready:
                errors.append(msg)
                break
        elif SEND_SETTLE > 0:
            time.sleep(SEND_SETTLE)
    return sent, errors


def broadcast_send(targets, text: str, submit: bool) -> list[dict]:
    """Send the same `text` to every tmux target in `targets`. Returns a list of
    per-target result dicts {target, ok, info|error}. Each target gets exactly
    one send, so this is immune to the multi-item concatenation the queue
    guards against."""
    results = []
    for t in targets:
        t = str(t)
        ok, info = inject(t, text, submit)
        r = {"target": t, "ok": ok}
        r["info" if ok else "error"] = info
        results.append(r)
    return results


# Named control keys allowed via /api/key. Values are tmux key names sent WITHOUT
# `-l`, so they are interpreted as real key presses (interrupts, not text).
ALLOWED_KEYS = {
    "C-c": "C-c",       # Ctrl-C — interrupt / cancel
    "Escape": "Escape",  # Esc — dismiss / stop the current CLI action
    "C-d": "C-d",       # Ctrl-D — EOF
    "C-u": "C-u",       # clear the current input line
    "Enter": "Enter",
    "Up": "Up",
    "Down": "Down",
}


def send_key(target: str, key: str) -> tuple[bool, str]:
    """Send a single named control key (from ALLOWED_KEYS) to a tmux target.
    Unlike inject(), this does NOT use `-l`, so the key is interpreted as a real
    key press (e.g. Ctrl-C interrupts instead of typing the literal text)."""
    tk = ALLOWED_KEYS.get(key)
    if tk is None:
        return False, f"key '{key}' not allowed"
    if not target_exists(target):
        return False, f"tmux target '{target}' not found"
    r = subprocess.run(["tmux", "send-keys", "-t", target, tk],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, (r.stderr or "send-keys failed").strip()
    return True, tk


def kill_session(name: str) -> tuple[bool, str]:
    """Kill an entire tmux session by name.

    Rejects empty names, names containing shell metacharacters, and names that
    don't currently exist as sessions. Also purges the session's persisted
    drafts so a re-created session with the same name starts clean.
    """
    n = (name or "").strip()
    if not n:
        return False, "session name required"
    if any(c in n for c in "\t\n\r;&|`$<>\\\"'"):
        return False, "invalid characters in session name"
    # Confirm it exists as a *session* (not just any target/pane addr).
    r = subprocess.run(["tmux", "has-session", "-t", n],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, f"tmux session '{n}' not found"
    r = subprocess.run(["tmux", "kill-session", "-t", n],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, (r.stderr or "kill-session failed").strip()
    # Best-effort: drop this session's persisted drafts so a fresh session with
    # the same name doesn't inherit the old queue.
    try:
        with _drafts_lock:
            data = _load_drafts()
            if n in data:
                del data[n]
                _save_drafts(data)
            if n in _autoflush:
                _autoflush.discard(n)
                _save_autoflush()
    except Exception:
        pass
    return True, n


# --- Spawn a new tmux session running cli-copilot ---------------------------
_SPAWN_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,63}$")
_SPAWN_MODEL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
SPAWN_LAUNCH_CMD = os.environ.get("VAAK_SPAWN_LAUNCH_CMD", "cli-copilot")
# Wait times (seconds) for cli-copilot to boot and accept slash commands.
SPAWN_BOOT_WAIT = float(os.environ.get("VAAK_SPAWN_BOOT_WAIT", "7.0"))
SPAWN_CMD_WAIT = float(os.environ.get("VAAK_SPAWN_CMD_WAIT", "1.5"))


def spawn_copilot_session(name: str, model_id: str = "") -> tuple[bool, str]:
    """Create a new tmux session, start cli-copilot in it, enable /allow-all
    (auto-approve tool/path/URL requests), and — if model_id is non-empty —
    select that model with `/model <id>`.

    Names are validated against a conservative allowlist (letters, digits,
    `._- `, up to 64 chars, must start alphanumeric). Model ids are validated
    against `[A-Za-z0-9._-]+` so they can't inject a slash command payload.
    """
    n = (name or "").strip()
    if not n:
        return False, "session name required"
    if not _SPAWN_NAME_OK.match(n):
        return False, "invalid session name (letters, digits, '._- ' only; ≤64 chars, must start alphanumeric)"
    if target_exists(n):
        return False, f"tmux session '{n}' already exists"
    mid = (model_id or "").strip()
    if mid and not _SPAWN_MODEL_OK.match(mid):
        return False, "invalid model id"

    r = subprocess.run(["tmux", "new", "-d", "-s", n],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return False, (r.stderr or "tmux new failed").strip()

    # From here on, failures should still surface but not orphan the session.
    def _send_literal(text: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", n, "-l", "--", text],
                       capture_output=True, check=False)

    def _send_enter() -> None:
        subprocess.run(["tmux", "send-keys", "-t", n, "Enter"],
                       capture_output=True, check=False)

    try:
        # Start the CLI. `cli-copilot` is a tcsh alias in the user's shell, so
        # letting tmux's default login shell resolve it is the right thing.
        _send_literal(SPAWN_LAUNCH_CMD)
        _send_enter()
        time.sleep(SPAWN_BOOT_WAIT)
        # Enable all-permissions mode.
        _send_literal("/allow-all")
        _send_enter()
        time.sleep(SPAWN_CMD_WAIT)
        # Select the recommended model, if one was specified.
        if mid:
            _send_literal("/model " + mid)
            _send_enter()
            time.sleep(SPAWN_CMD_WAIT)
    except Exception as e:
        return False, f"spawn steps failed: {e}"
    return True, n


# --- Auto-flush poller ------------------------------------------------------
# For sessions with auto-flush enabled, watch for a busy->ready transition and
# then send exactly one queued draft (the head of the queue). We require the
# session to have been observed busy at least once, or simply ready with items,
# and we send one item per ready tick (then it goes busy again on submit).
def _autoflush_loop() -> None:
    last_status: dict[str, str] = {}
    while True:
        time.sleep(AUTOFLUSH_INTERVAL)
        try:
            sessions = {s["name"]: s["status"] for s in list_sessions()}
        except Exception:
            continue
        for sess in list(_autoflush):
            st = sessions.get(sess)
            if st is None:                      # session gone
                with _drafts_lock:
                    _autoflush.discard(sess)
                    _save_autoflush()
                last_status.pop(sess, None)
                continue
            prev = last_status.get(sess)
            last_status[sess] = st
            if st != "ready":
                continue
            # Only fire on entering ready (or first observation), never while busy
            if prev == "busy" or prev is None:
                items = get_drafts(sess)
                if not items:
                    continue
                time.sleep(AUTOFLUSH_GRACE)
                if session_status(sess) != "ready":
                    continue
                first = items[0]
                sent_ok, _info, saw_busy, _accepted = send_queued_item(
                    sess, first, submit=True)
                if sent_ok:
                    delete_draft(sess, first["id"])
                # Whether or not acceptance was confirmed, do not immediately
                # send another item while the target may still be settling.
                last_status[sess] = "busy" if saw_busy else session_status(sess)


def start_autoflush() -> None:
    t = threading.Thread(target=_autoflush_loop, name="vaak-autoflush",
                         daemon=True)
    t.start()


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vaak \u2192 CLI</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,Segoe UI,sans-serif;
 display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{padding:9px 16px;background:#161b22;border-bottom:1px solid #30363d;
 display:flex;align-items:center;gap:10px;flex:0 0 auto}
header b{color:#58a6ff}
header .sub{color:#8b949e;font-size:12px}
#wrap{flex:1;display:flex;min-height:0}
#nav{width:230px;flex:0 0 auto;background:#12161d;border-right:1px solid #30363d;
 overflow:auto;display:flex;flex-direction:column}
#nav h3{margin:0;padding:9px 12px;font-size:11px;letter-spacing:.6px;text-transform:uppercase;
 color:#8b949e;border-bottom:1px solid #30363d;display:flex;justify-content:space-between}
.sess{padding:9px 12px;border-bottom:1px solid #1c222b;cursor:pointer;display:flex;
 align-items:center;gap:8px}
.sess:hover{background:#ffffff0d}
.sess.active{background:#1f6feb26;border-left:3px solid #58a6ff;padding-left:9px}
.dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto;background:#8b949e}
.sess .bx{margin:0 3px 0 0;flex:0 0 auto;cursor:pointer;width:15px;height:15px}
#bcastRow{border-top:1px dashed #30363d;padding-top:8px}
.dot.ready{background:#3fb950}.dot.busy{background:#d29922}.dot.gone{background:#f85149}
.sess .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
.sess .cmd{color:#8b949e;font-size:11px;font-family:monospace}
.badge{background:#3b2a5f;color:#ddd6fe;border:1px solid #5b21b6;border-radius:10px;
 font-size:11px;padding:0 7px;min-width:18px;text-align:center}
.badge.zero{display:none}
.sess .kill{flex:0 0 auto;background:transparent;border:1px solid transparent;color:#8b949e;
 border-radius:6px;padding:0 6px;font-size:14px;line-height:20px;cursor:pointer;font-weight:600;
 opacity:0;transition:opacity .12s,color .12s,border-color .12s}
.sess:hover .kill,.sess.active .kill{opacity:1}
.sess .kill:hover{color:#f85149;border-color:#f85149;background:#f851491a}
main{flex:1;display:flex;flex-direction:column;gap:10px;padding:14px;min-width:0;overflow:auto}
.stbar{display:flex;align-items:center;gap:8px;font-size:14px}
.stbar .pill{font-size:12px;padding:2px 9px;border-radius:11px;border:1px solid #30363d}
.pill.ready{background:#12331f;color:#3fb950;border-color:#238636}
.pill.busy{background:#3a2f12;color:#e3b341;border-color:#7a5c17}
.pill.gone{background:#3a1414;color:#f85149;border-color:#7a1717}
#msg{width:100%;min-height:26vh;font-size:19px;line-height:1.4;padding:13px;
 border-radius:10px;border:1px solid #30363d;background:#0b0f14;color:#e6edf3;resize:vertical}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
button{font-size:15px;padding:10px 16px;border-radius:9px;border:1px solid #238636;
 background:#238636;color:#fff;cursor:pointer;font-weight:600}
button:active{transform:translateY(1px)}
button.sec{background:#21262d;border-color:#30363d;color:#e6edf3}
button.mini{font-size:12px;padding:4px 9px;font-weight:500}
label{color:#8b949e;font-size:13px;display:flex;align-items:center;gap:5px}
.hint{color:#8b949e;font-size:12px}
h4{margin:6px 0 0;font-size:12px;letter-spacing:.5px;text-transform:uppercase;color:#8b949e;
 display:flex;align-items:center;gap:8px}
#queue{display:flex;flex-direction:column;gap:7px}
.qi{border:1px solid #30363d;border-radius:8px;background:#161b22;padding:8px 10px;
 display:flex;gap:8px;align-items:flex-start}
.qi .qt{flex:1;white-space:pre-wrap;word-break:break-word;font-size:14px;min-width:0;cursor:text}
.qi .qt:hover{background:#ffffff08;border-radius:4px}
.qi.editing{border-color:#58a6ff;box-shadow:0 0 0 2px #58a6ff33;flex-direction:column;align-items:stretch;gap:6px}
.qi.editing .qt{display:none}
.qi.editing .qa{display:none}
.qi .qedit{display:none;flex-direction:column;gap:6px;width:100%}
.qi.editing .qedit{display:flex}
.qi .qedit textarea{width:100%;min-height:60px;background:#0b0f14;color:#e6edf3;border:1px solid #30363d;
 border-radius:6px;padding:8px 10px;font:14px/1.4 inherit;resize:vertical;box-sizing:border-box}
.qi .qedit textarea:focus{outline:none;border-color:#58a6ff}
.qi .qedit .qhint{color:#8b949e;font-size:11px}
.qi .qedit .qactions{display:flex;gap:6px;justify-content:flex-end}
.qi .qa{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.empty{color:#8b949e;font-style:italic;font-size:13px}
#paneWrap{border:1px solid #30363d;border-radius:10px;background:#06090f;
 display:flex;flex-direction:column;min-height:0;flex:0 0 auto}
#paneHead{display:flex;align-items:center;gap:8px;justify-content:space-between;padding:7px 10px;
 border-bottom:1px solid #30363d;color:#8b949e;font-size:12px;flex:0 0 auto;
 border-radius:10px 10px 0 0;background:#06090f}
#pane{margin:0;padding:10px 10px 18px;max-height:50vh;min-height:20vh;overflow:auto;
 white-space:pre-wrap;word-break:break-word;
 font:12px/1.35 ui-monospace,SFMono-Regular,Consolas,Liberation Mono,monospace;color:#c9d1d9;
 border-radius:0 0 10px 10px}
#toasts{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;z-index:9}
.toast{background:#12331f;color:#d1f7d6;border:1px solid #238636;border-radius:10px;
 padding:10px 12px;box-shadow:0 8px 24px #0008;font-size:14px}
#log{font-family:monospace;font-size:12px;color:#8b949e;white-space:pre-wrap;
 border-top:1px solid #30363d;padding-top:7px;max-height:16vh;overflow:auto;flex:0 0 auto}
.ok{color:#3fb950}.err{color:#f85149}
/* QR modal */
#qrModal{position:fixed;inset:0;background:#000a;display:none;align-items:center;
 justify-content:center;z-index:50}
#qrModal.show{display:flex}
#qrCard{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;
 text-align:center;max-width:90vw}
/* Model guide modal */
#mgModal{position:fixed;inset:0;background:#000b;display:none;align-items:flex-start;
 justify-content:center;z-index:60;overflow:auto;padding:20px 14px 48px}
#mgModal.show{display:flex}
#mgCard{width:100%;max-width:1040px;position:relative}
#mgClose{position:fixed;top:16px;right:20px;z-index:61;background:#21262d;border:1px solid #30363d;
 color:#e6edf3;border-radius:8px;padding:6px 12px;font-size:13px;font-weight:600;cursor:pointer}
#mgClose:hover{border-color:#58a6ff}
/* Model guide inner styles (scoped inside #mgCard) */
#mgCard .mg-banner{background:linear-gradient(135deg,#1a2540 0%,#141925 60%);border:1px solid #2a3446;
 border-radius:14px;padding:18px 20px;box-shadow:0 10px 30px -12px #000a;position:relative;overflow:hidden}
#mgCard .mg-banner::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,#6ea8fe10,transparent);pointer-events:none}
#mgCard .mg-brand{display:flex;align-items:center;gap:12px;margin-bottom:4px}
#mgCard .mg-logo{width:34px;height:34px;border-radius:9px;flex:0 0 auto;background:linear-gradient(135deg,#6ea8fe,#8b7bff);display:grid;place-items:center;font-weight:800;color:#0b0e14;box-shadow:0 4px 14px -4px #6ea8fe80}
#mgCard h1.mg-h1{font-size:20px;margin:0;letter-spacing:.2px;color:#e6edf3}
#mgCard .mg-sub{color:#94a3b8;font-size:13px;margin:2px 0 0}
#mgCard .mg-field{margin-top:16px}
#mgCard .mg-label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin-bottom:7px;font-weight:600}
#mgCard .mg-picker{display:grid;grid-template-columns:minmax(310px,36%) 1fr;gap:16px;align-items:stretch}
#mgCard .mg-rail{background:#0f141d;border:1px solid #2a3446;border-radius:14px;padding:8px;display:grid;grid-template-rows:repeat(7,minmax(0,1fr));gap:6px;min-height:410px;box-sizing:border-box}
#mgCard .mg-tile{appearance:none;border:1px solid transparent;background:transparent;color:#e6edf3;border-radius:10px;padding:9px 10px;text-align:left;cursor:pointer;display:grid;grid-template-columns:30px 1fr 18px;align-items:center;gap:9px;transition:background .15s,border-color .15s,box-shadow .15s,transform .15s}
#mgCard .mg-tile:hover{background:#6ea8fe14;border-color:#34445d}
#mgCard .mg-tile:focus-visible{outline:none;border-color:#6ea8fe;box-shadow:0 0 0 3px #6ea8fe66}
#mgCard .mg-tile[aria-checked="true"]{background:linear-gradient(135deg,#1f6feb30,#8b7bff1f);border-color:#5b83c7;box-shadow:inset 3px 0 0 #6ea8fe}
#mgCard .mg-tile .mg-icon{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;color:#8bbcff;background:#6ea8fe14;border:1px solid #2a3446}
#mgCard .mg-tile[aria-checked="true"] .mg-icon{background:linear-gradient(135deg,#6ea8fe,#8b7bff);color:#0b0e14;border-color:transparent}
#mgCard .mg-tile .mg-tt{min-width:0}
#mgCard .mg-tile .mg-tt b{display:block;font-size:13.5px;font-weight:700;line-height:1.18;color:#e6edf3}
#mgCard .mg-tile .mg-tt small{display:block;color:#94a3b8;font-size:11.5px;line-height:1.2;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#mgCard .mg-tile .mg-check{color:#7db3ff;opacity:0;font-weight:800}
#mgCard .mg-tile[aria-checked="true"] .mg-check{opacity:1}
#mgCard .mg-result{margin-top:0;background:#141925;border:1px solid #2a3446;border-radius:14px;box-shadow:0 10px 30px -12px #000a;overflow:hidden;min-height:410px;display:flex;flex-direction:column;position:relative;box-sizing:border-box}
#mgCard .mg-result::after{content:"";position:absolute;right:-48px;bottom:-56px;width:180px;height:180px;border-radius:50%;background:#6ea8fe0d;pointer-events:none}
#mgCard .mg-rhead{padding:22px 24px 16px;border-bottom:1px solid #2a3446;background:radial-gradient(circle at 24px 20px,#6ea8fe18,transparent 130px),linear-gradient(180deg,#182034,transparent)}
#mgCard .mg-rtask{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.6px;font-weight:600}
#mgCard .mg-rmodel{font-size:24px;font-weight:800;margin:6px 0 2px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
#mgCard .mg-rmodel .mg-icon{display:inline-flex;color:#6ea8fe}
#mgCard .mg-pri{background:linear-gradient(135deg,#6ea8fe,#8b7bff);-webkit-background-clip:text;background-clip:text;color:transparent}
#mgCard .mg-badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
#mgCard .mg-badge{font-size:12px;font-weight:600;padding:4px 10px;border-radius:20px;border:1px solid #2a3446;display:inline-flex;align-items:center;gap:6px;background:#1b2230}
#mgCard .mg-badge .k{color:#94a3b8;font-weight:500}
#mgCard .mg-badge.eff{border-color:#7a5c17;background:#3a2f12;color:#e3b341}
#mgCard .mg-badge.ctx{border-color:#2b4b7a;background:#12233a;color:#7db3ff}
#mgCard .mg-rbody{padding:18px 24px 22px;display:flex;flex-direction:column;flex:1}
#mgCard .mg-why{color:#cbd5e1}
#mgCard .mg-why b{color:#e6edf3}
#mgCard .mg-alts{margin-top:16px}
#mgCard .mg-alts .h{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:#94a3b8;margin-bottom:8px;font-weight:600}
#mgCard .mg-chips{display:flex;gap:8px;flex-wrap:wrap}
#mgCard .mg-chip{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;padding:5px 10px;border-radius:8px;background:#1b2230;border:1px solid #2a3446;color:#cbd5e1}
#mgCard .mg-cmd{margin-top:auto;padding-top:18px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:relative;z-index:1}
#mgCard .mg-cmdbox{font-family:ui-monospace,Consolas,monospace;font-size:14px;background:#0a0d13;border:1px solid #2a3446;border-radius:9px;padding:9px 12px;color:#a5d6ff;flex:1;min-width:200px}
#mgCard .mg-copy{background:#1b2230;border:1px solid #2a3446;color:#e6edf3;border-radius:9px;padding:9px 14px;font-weight:600;cursor:pointer;transition:border-color .15s,background .15s}
#mgCard .mg-copy:hover{border-color:#6ea8fe}
#mgCard .mg-copy.done{border-color:#3fb950;color:#3fb950}
#mgCard .mg-launch{background:linear-gradient(135deg,#238636,#2ea043);border-color:#2ea043;color:#fff}
#mgCard .mg-launch:hover{border-color:#3fb950;background:linear-gradient(135deg,#2ea043,#3fb950)}
#mgCard .mg-launch:disabled{opacity:.65;cursor:progress;background:#1b2230;border-color:#2a3446;color:#8b949e}
#mgCard .mg-launch.done{background:#1b2230;border-color:#3fb950;color:#3fb950}
#mgCard .mg-section{margin-top:18px}
#mgCard .mg-section h2{font-size:13px;text-transform:uppercase;letter-spacing:.7px;color:#94a3b8;margin:0 0 10px}
#mgCard .mg-rules{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
#mgCard .mg-rule{display:flex;gap:12px;background:#141925;border:1px solid #2a3446;border-radius:11px;padding:12px 14px}
#mgCard .mg-rule .n{width:22px;height:22px;border-radius:7px;flex:0 0 auto;display:grid;place-items:center;font-size:12px;font-weight:800;color:#0b0e14;background:linear-gradient(135deg,#6ea8fe,#8b7bff)}
#mgCard .mg-rule p{margin:0;color:#cbd5e1;font-size:14px}
#mgCard .mg-rule b{color:#e6edf3}
#mgCard .mg-tabtoggle{background:none;border:1px solid #2a3446;color:#94a3b8;border-radius:9px;padding:7px 12px;cursor:pointer;font-weight:600;font-size:13px}
#mgCard .mg-tabtoggle:hover{color:#e6edf3;border-color:#6ea8fe}
#mgCard table.mg-table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13.5px;display:none}
#mgCard table.mg-table.show{display:table}
#mgCard table.mg-table th,#mgCard table.mg-table td{text-align:left;padding:10px 12px;border-bottom:1px solid #2a3446;vertical-align:top}
#mgCard table.mg-table th{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.5px;position:sticky;top:0;background:#141925}
#mgCard table.mg-table tbody tr:hover{background:#6ea8fe0f}
#mgCard table.mg-table td .m{font-family:ui-monospace,Consolas,monospace;color:#a5d6ff}
#mgCard .mg-legend{margin-top:22px;color:#64748b;font-size:12.5px;display:flex;gap:18px;flex-wrap:wrap}
#mgCard .mg-legend b{color:#94a3b8}
#mgCard .mg-foot{margin-top:24px;color:#64748b;font-size:12px;text-align:center}
@media (max-width:780px){
 #mgCard{max-width:620px}
 #mgCard .mg-picker{grid-template-columns:1fr}
 #mgCard .mg-rail{min-height:0;grid-template-rows:none}
 #mgCard .mg-result{min-height:0}
 #mgCard .mg-rules{grid-template-columns:1fr}
}
#qrCard h3{margin:0 0 12px;color:#58a6ff}
#qrImg{background:#fff;padding:12px;border-radius:8px;display:inline-block}
#qrUrl{margin-top:12px;color:#8b949e;font-family:monospace;font-size:12px;word-break:break-all;max-width:320px}
#qrClose{margin-top:14px}
/* Mobile / responsive: stack the nav on top and enlarge touch targets */
@media (max-width:640px){
 #wrap{flex-direction:column}
 #nav{width:100%;max-height:38vh;border-right:0;border-bottom:1px solid #30363d}
 main{padding:12px}
 #msg{min-height:22vh;font-size:18px}
 button{padding:12px 18px;font-size:16px}
 button.mini{padding:8px 12px;font-size:14px}
 .qi .qa{gap:6px}
 header .sub{display:none}
}
</style></head><body>
<header><b>Vaak</b> <span class="sub">\u2192 dictate &amp; queue into tmux CLI sessions</span>
 <button class="sec mini" id="mgBtn" style="margin-left:auto" title="AI model selection guide">&#x1F9E0; Model Guide</button>
 <button class="sec mini" id="qrBtn" style="margin-left:8px" title="Open on your phone">QR</button></header>
<div id="qrModal"><div id="qrCard">
 <h3>Open Vaak on your phone</h3>
 <div id="qrImg"></div>
 <div id="qrUrl"></div>
 <div class="hint" style="margin-top:8px">Scan with your phone camera (same network / VPN).</div>
 <button class="sec" id="qrClose">Close</button>
</div></div>
<div id="mgModal">
 <button id="mgClose">\u00d7 Close</button>
 <div id="mgCard">
  <div class="mg-banner">
   <div class="mg-brand">
    <div class="mg-logo">AI</div>
    <div><h1 class="mg-h1">Copilot Model Picker</h1>
     <p class="mg-sub">Pick your task &rarr; get the right model, effort &amp; context tier.</p></div>
   </div>
   <div class="mg-field">
    <div class="mg-label" id="mgPickerLabel">Choose the closest task</div>
    <div class="mg-picker">
     <div class="mg-rail" id="mgRail" role="radiogroup" aria-labelledby="mgPickerLabel"></div>
     <div class="mg-result" id="mgResult" aria-live="polite">
      <div class="mg-rhead">
       <div class="mg-rtask" id="mgRTask"></div>
       <div class="mg-rmodel"><span class="mg-icon" id="mgRIcon"></span><span class="mg-pri" id="mgRModel"></span></div>
       <div class="mg-badges" id="mgRBadges"></div>
      </div>
      <div class="mg-rbody">
       <div class="mg-why" id="mgRWhy"></div>
       <div class="mg-alts"><div class="h">Also good</div><div class="mg-chips" id="mgRAlts"></div></div>
       <div class="mg-cmd">
        <span class="mg-cmdbox" id="mgRCmd"></span>
        <button class="mg-copy" id="mgCopyBtn" title="Copy the /model command">Copy</button>
        <button class="mg-copy mg-launch" id="mgLaunchBtn" title="Create a new tmux session, launch cli-copilot, /allow-all, and select this model">🚀 Launch session</button>
       </div>
      </div>
     </div>
    </div>
   </div>
  </div>
  <div class="mg-section"><h2>Rules of thumb</h2><div class="mg-rules" id="mgRules"></div></div>
  <div class="mg-section">
   <div style="display:flex;align-items:center;justify-content:space-between;gap:10px">
    <h2 style="margin:0">Full comparison</h2>
    <button class="mg-tabtoggle" id="mgTabToggle" aria-expanded="false">Show table</button>
   </div>
   <table class="mg-table" id="mgTable">
    <thead><tr><th>Task</th><th>Model</th><th>Effort</th><th>Context</th></tr></thead>
    <tbody id="mgTbody"></tbody>
   </table>
  </div>
  <div class="mg-legend">
   <span><b>Effort</b> &mdash; reasoning depth (low &rarr; max). Raise it before switching models.</span>
   <span><b>Context</b> &mdash; default vs long_context (whole-repo / long docs).</span>
  </div>
  <p class="mg-foot">Switch anytime with <code>/model &lt;id&gt;</code> in the CLI. Reference only &mdash; availability may vary by plan.</p>
 </div>
</div>
<div id="wrap">
  <div id="nav">
    <h3><span>Sessions</span><span id="navcount"></span></h3>
    <div id="sesslist"></div>
    <div style="margin-top:auto;padding:8px 12px;border-top:1px solid #30363d">
      <button class="sec mini" id="rescan" style="width:100%">\u21bb Rescan</button>
    </div>
  </div>
  <main>
    <div class="stbar">
      <b id="selName">no session</b>
      <span class="pill" id="selStatus">\u2014</span>
      <span class="hint" id="selCmd"></span>
    </div>
    <textarea id="msg" placeholder="Click here, press Win+H, and speak\u2026 Enter sends now; or Add to queue while the session is busy."></textarea>
    <div class="row">
      <button id="sendNow">Send now \u2192</button>
      <button class="sec" id="addQ">+ Add to queue</button>
      <button class="sec" id="clear">Clear</button>
      <span style="width:1px;height:22px;background:#30363d;margin:0 2px"></span>
      <button class="sec" id="ctrlC" title="Send Ctrl-C (interrupt) to the selected session">^C</button>
      <button class="sec" id="escKey" title="Send Esc to the selected session">Esc</button>
      <label><input type="checkbox" id="enterSends" checked> Enter = send now</label>
      <label><input type="checkbox" id="submit" checked> Submit in CLI</label>
      <label><input type="checkbox" id="keep" checked> Keep focus</label>
      <label><input type="checkbox" id="readyAlert" checked> Alert when ready</label>
    </div>
    <div class="row" id="bcastRow">
      <button class="sec" id="bcastBtn">Broadcast to selected (0) \u2192</button>
      <span class="hint">tick sessions in the list \u00b7</span>
      <button class="mini sec" id="bcastAll">All</button>
      <button class="mini sec" id="bcastReady">All ready</button>
      <button class="mini sec" id="bcastNone">None</button>
    </div>
    <div id="paneTools" class="row">
      <button class="sec mini" id="togglePane">Hide terminal mirror</button>
      <label class="hint">scrollback
        <select id="paneLines" style="background:#0b0f14;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:2px 6px;font-size:12px">
          <option value="40">40</option><option value="200">200</option>
          <option value="500">500</option><option value="1000">1000</option>
          <option value="2000">2000</option><option value="5000">5000</option>
        </select> lines
      </label>
      <label class="hint">font
        <select id="paneFont" style="background:#0b0f14;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:2px 6px;font-size:12px">
          <option>10</option><option>11</option><option>12</option><option>13</option>
          <option>14</option><option>16</option><option>18</option><option>20</option>
        </select> px
      </label>
      <span class="hint" id="paneMeta">terminal mirror polls every 1.5s</span>
    </div>
    <div id="paneWrap">
      <div id="paneHead"><b>Terminal mirror</b><span class="hint">read-only tmux capture-pane</span></div>
      <pre id="pane"></pre>
    </div>
    <h4><span>Queue</span>
      <button class="mini" id="sendAll">Send all in order</button>
      <label style="font-weight:400"><input type="checkbox" id="autoflush"> Auto-send when ready</label>
    </h4>
    <div id="queue"></div>
    <div id="log"></div>
  </main>
</div>
<div id="toasts"></div>
<script>
const $=s=>document.querySelector(s);
const qs=new URLSearchParams(location.search);
const token=qs.get('token')||'';
const msg=$('#msg'), log=$('#log');
let sel=null;              // selected session name
let sessions=[];
const bcast=new Set();     // session names selected for broadcast
let paneVisible=localStorage.getItem('vaakPaneVisible')!=='0';
let paneLines=parseInt(localStorage.getItem('vaakPaneLines')||'1000',10)||1000;
let paneFont=parseInt(localStorage.getItem('vaakPaneFont')||'12',10)||12;
const prevStatus={};
const originalTitle=document.title;
let titleFlashTimer=null;
let audioCtx=null;
function logline(t,cls){const d=document.createElement('div');if(cls)d.className=cls;
  d.textContent=new Date().toLocaleTimeString()+'  '+t;log.prepend(d);}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function stopTitleFlash(){
  if(titleFlashTimer){clearInterval(titleFlashTimer);titleFlashTimer=null;}
  document.title=originalTitle;
}
function flashTitle(name){
  stopTitleFlash();
  let on=false,ticks=0;
  titleFlashTimer=setInterval(()=>{
    document.title=on?originalTitle:('● ready — '+name);
    on=!on;
    if(++ticks>=16)stopTitleFlash();
  },700);
}
function beep(){
  try{
    const C=window.AudioContext||window.webkitAudioContext;
    if(!C)return;
    audioCtx=audioCtx||new C();
    const osc=audioCtx.createOscillator(), gain=audioCtx.createGain();
    osc.type='sine';osc.frequency.value=880;gain.gain.value=0.06;
    osc.connect(gain);gain.connect(audioCtx.destination);
    osc.start();osc.stop(audioCtx.currentTime+0.14);
  }catch(e){logline('ready beep unavailable: '+e.message,'err');}
}
function toast(text){
  const d=document.createElement('div');d.className='toast';d.textContent=text;
  $('#toasts').appendChild(d);setTimeout(()=>d.remove(),6000);
}
function readyAlertsOn(){return $('#readyAlert')&&$('#readyAlert').checked;}
function fireReadyAlert(name){
  if(!readyAlertsOn())return;
  logline(name+' is ready','ok');toast('Ready: '+name);flashTitle(name);beep();
}
async function copyText(text){
  let detail='';
  if(navigator.clipboard&&navigator.clipboard.writeText){
    try{await navigator.clipboard.writeText(text);logline('copied item to clipboard (clipboard API)','ok');return true;}
    catch(e){detail=e.message||String(e);}
  }
  const ta=document.createElement('textarea');
  ta.value=text;ta.setAttribute('readonly','');
  ta.style.position='fixed';ta.style.left='-1000px';ta.style.top='0';
  document.body.appendChild(ta);ta.focus();ta.select();
  try{
    if(document.execCommand('copy')){logline('copied item to clipboard (fallback)','ok');return true;}
    logline('copy failed'+(detail?': '+detail:''),'err');return false;
  }catch(e){logline('copy failed: '+(detail||e.message),'err');return false;}
  finally{document.body.removeChild(ta);}
}
function updatePaneVisibility(){
  $('#paneWrap').style.display=paneVisible?'block':'none';
  $('#togglePane').textContent=paneVisible?'Hide terminal mirror':'Show terminal mirror';
  localStorage.setItem('vaakPaneVisible',paneVisible?'1':'0');
  if(paneVisible)loadPane();
}
async function loadPane(){
  if(!sel||!paneVisible)return;
  try{
    const r=await fetch('/api/pane?target='+encodeURIComponent(sel)+'&lines='+paneLines);
    const d=await r.json();
    if(!r.ok){$('#paneMeta').textContent='mirror error: '+(d.error||r.status);return;}
    const pre=$('#pane');
    // Stick to the bottom only if the user is already near it; otherwise keep
    // their scroll position so scrolling up to read isn't yanked back down.
    const nearBottom=(pre.scrollHeight-pre.scrollTop-pre.clientHeight)<40;
    const prevTop=pre.scrollTop;
    pre.textContent=(d.lines||[]).join('\\n');
    if(nearBottom){
      requestAnimationFrame(()=>{pre.scrollTop=pre.scrollHeight;});
    }else{
      pre.scrollTop=prevTop;
    }
    $('#paneMeta').textContent='showing '+(d.lines||[]).length+' lines from '+d.target+(nearBottom?'':' \\u00b7 scroll-lock (scroll down to follow)');
  }catch(e){$('#paneMeta').textContent='mirror error: '+e.message;}
}

async function api(path,body){
  const opt=body?{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({token},body))}:undefined;
  const r=await fetch(path,opt);
  return r.json();
}

async function loadSessions(){
  try{
    const d=await api('/api/sessions');
    sessions=d.sessions||[];
    sessions.forEach(s=>{
      const prev=prevStatus[s.name];
      if(prev==='busy'&&s.status==='ready')fireReadyAlert(s.name);
      prevStatus[s.name]=s.status;
    });
    $('#navcount').textContent=sessions.length;
    if(!sel && sessions.length){sel=(sessions.find(s=>s.name===d.default)||sessions[0]).name;}
    renderNav();
    const cur=sessions.find(s=>s.name===sel);
    if(cur){renderStatus(cur);loadPane();}
  }catch(e){logline('sessions error: '+e.message,'err');}
}
function renderNav(){
  const el=$('#sesslist');
  if(!sessions.length){el.innerHTML='<div class="sess"><span class="nm empty">No tmux sessions. Start one: tmux new -s copilot</span></div>';return;}
  el.innerHTML='';
  sessions.forEach(s=>{
    const d=document.createElement('div');
    d.className='sess'+(s.name===sel?' active':'');
    d.innerHTML=`<input type="checkbox" class="bx" ${bcast.has(s.name)?'checked':''} title="Include in broadcast">`+
      `<span class="dot ${s.status}"></span>`+
      `<span class="nm">${esc(s.name)} <span class="cmd">${esc(s.command)}</span></span>`+
      `<span class="badge ${s.drafts?'':'zero'}">${s.drafts}</span>`+
      `<button class="kill" title="Kill this tmux session (tmux kill-session)" aria-label="Kill session ${esc(s.name)}">\u2715</button>`;
    const bx=d.querySelector('.bx');
    bx.onclick=(e)=>{e.stopPropagation();
      if(bx.checked)bcast.add(s.name); else bcast.delete(s.name); updateBcastBtn();};
    const kb=d.querySelector('.kill');
    kb.onclick=async(e)=>{
      e.stopPropagation();
      const n=s.name;
      const nDrafts=s.drafts|0;
      const warn=`Kill tmux session "${n}"?\n\nThis runs \`tmux kill-session\` and closes any CLI running inside it.`+
                 (nDrafts?`\n\nThe ${nDrafts} queued draft(s) for this session will also be discarded.`:'');
      if(!confirm(warn))return;
      try{
        const r=await api('/api/kill_session',{session:n});
        if(r&&r.ok){toast(`Killed session ${n}`);logline('killed session '+n,'ok');
          bcast.delete(n);
          if(sel===n){sel='';$('#selName').textContent='no session';$('#selStatus').textContent='\u2014';$('#selStatus').className='pill';$('#selCmd').textContent='';$('#pane').textContent='';$('#queue').innerHTML='';}
          loadSessions();
        } else {toast(`Kill failed: ${r&&r.error||'unknown'}`,'err');}
      }catch(err){toast('Kill error: '+err.message,'err');}
    };
    d.onclick=()=>{sel=s.name;renderNav();renderStatus(s);loadDrafts();loadPane();msg.focus();};
    el.appendChild(d);
  });
  updateBcastBtn();
}
function renderStatus(s){
  $('#selName').textContent=s.name;
  const p=$('#selStatus');p.textContent=s.status;p.className='pill '+s.status;
  $('#selCmd').textContent=s.command?('['+s.command+']'):'';
  $('#autoflush').checked=!!s.autoflush;
}

async function loadDrafts(){
  if(!sel){$('#queue').innerHTML='';return;}
  try{
    const d=await api('/api/drafts?session='+encodeURIComponent(sel));
    $('#autoflush').checked=!!d.autoflush;
    renderQueue(d.drafts||[]);
  }catch(e){logline('drafts error: '+e.message,'err');}
}
function renderQueue(items){
  const el=$('#queue');
  if(!items.length){el.innerHTML='<div class="empty">No queued items. Type above and \u201cAdd to queue\u201d \u2014 works even while the session is busy.</div>';return;}
  el.innerHTML='';
  items.forEach((it,i)=>{
    const d=document.createElement('div');d.className='qi';d.dataset.id=it.id;
    d.innerHTML=`<span class="qt" title="Click to edit">${esc(it.text)}</span>`+
      `<span class="qa">`+
      `<button class="sec mini" data-a="edit">\u270e Edit</button>`+
      `<button class="sec mini" data-a="copy">Copy</button>`+
      `<button class="mini" data-a="send">Send</button>`+
      `<button class="sec mini" data-a="up" ${i===0?'disabled':''}>\u2191</button>`+
      `<button class="sec mini" data-a="down" ${i===items.length-1?'disabled':''}>\u2193</button>`+
      `<button class="sec mini" data-a="del">\u2715</button>`+
      `</span>`+
      `<div class="qedit">`+
      `<textarea></textarea>`+
      `<div class="qhint">Enter to save \u00b7 Shift+Enter for newline \u00b7 Esc to cancel</div>`+
      `<div class="qactions">`+
      `<button class="sec mini" data-a="cancel">Cancel</button>`+
      `<button class="mini" data-a="save">Save</button>`+
      `</div></div>`;
    d.querySelector('[data-a=copy]').onclick=()=>copyText(it.text);
    d.querySelector('[data-a=send]').onclick=()=>sendDraft(it.id);
    d.querySelector('[data-a=up]').onclick=()=>moveDraft(it.id,'up');
    d.querySelector('[data-a=down]').onclick=()=>moveDraft(it.id,'down');
    d.querySelector('[data-a=del]').onclick=()=>delDraft(it.id);
    d.querySelector('[data-a=edit]').onclick=()=>beginEdit(d,it.text);
    d.querySelector('[data-a=cancel]').onclick=()=>cancelEdit(d);
    d.querySelector('[data-a=save]').onclick=()=>saveEdit(d,it.id);
    d.querySelector('.qt').onclick=()=>beginEdit(d,it.text);
    const ta=d.querySelector('.qedit textarea');
    ta.onkeydown=(e)=>{
      if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();saveEdit(d,it.id);}
      else if(e.key==='Escape'){e.preventDefault();cancelEdit(d);}
    };
    el.appendChild(d);
  });
}
function beginEdit(row,text){
  const el=$('#queue');
  el.querySelectorAll('.qi.editing').forEach(r=>{if(r!==row)r.classList.remove('editing');});
  row.classList.add('editing');
  const ta=row.querySelector('.qedit textarea');
  ta.value=text;
  const rows=Math.max(2,Math.min(10,(text.match(/\\n/g)||[]).length+2));
  ta.rows=rows;
  ta.focus();ta.setSelectionRange(ta.value.length,ta.value.length);
}
function cancelEdit(row){row.classList.remove('editing');}
async function saveEdit(row,id){
  const ta=row.querySelector('.qedit textarea');
  const text=ta.value.trim();
  if(!text){logline('empty text \u2014 use \u2715 to delete instead','err');return;}
  try{
    const d=await api('/api/drafts/update',{session:sel,id,text});
    if(d&&d.ok){logline('edited draft','ok');renderQueue(d.drafts||[]);}
    else logline('edit failed: '+((d&&d.error)||'unknown'),'err');
  }catch(e){logline('edit error: '+e.message,'err');}
}

async function sendNow(){
  if(!sel){logline('pick a session first','err');return;}
  const text=msg.value;if(!text.trim())return;
  try{
    const d=await api('/api/send',{text,target:sel,submit:$('#submit').checked});
    if(d.ok){logline('sent now \u2192 '+sel+': '+d.injected,'ok');msg.value='';}
    else logline('FAILED: '+d.error,'err');
  }catch(e){logline('send error: '+e.message,'err');}
  if($('#keep').checked)msg.focus();
  loadSessions();
}
function updateBcastBtn(){
  const b=$('#bcastBtn'); if(b)b.textContent='Broadcast to selected ('+bcast.size+') \\u2192';
}
function selectBcast(mode){
  bcast.clear();
  sessions.forEach(s=>{if(mode==='all'||(mode==='ready'&&s.status==='ready'))bcast.add(s.name);});
  renderNav();
}
async function broadcast(){
  const text=msg.value;
  if(!text.trim()){logline('type a command to broadcast','err');return;}
  const targets=[...bcast];
  if(!targets.length){logline('no sessions ticked for broadcast','err');return;}
  try{
    const d=await api('/api/broadcast',{text,targets,submit:$('#submit').checked});
    const oks=(d.results||[]).filter(r=>r.ok).map(r=>r.target);
    const bad=(d.results||[]).filter(r=>!r.ok);
    logline('broadcast: '+d.sent+'/'+d.count+' \\u2192 ['+oks.join(', ')+']'+
      (bad.length?(' \\u00b7 FAILED: '+bad.map(r=>r.target+' ('+r.error+')').join(', ')):''),
      bad.length?'err':'ok');
    if(!bad.length)msg.value='';
  }catch(e){logline('broadcast error: '+e.message,'err');}
  if($('#keep').checked)msg.focus();
  loadSessions();
}
async function addToQueue(){
  if(!sel){logline('pick a session first','err');return;}
  const text=msg.value;if(!text.trim())return;
  try{
    const d=await api('/api/drafts/add',{session:sel,text});
    if(d.ok){logline('queued for '+sel,'ok');msg.value='';renderQueue(d.drafts);loadSessions();}
    else logline('queue FAILED: '+(d.error||''),'err');
  }catch(e){logline('queue error: '+e.message,'err');}
  if($('#keep').checked)msg.focus();
}
async function sendDraft(id){
  try{
    const d=await api('/api/drafts/send',{session:sel,id,submit:$('#submit').checked});
    if(d.ok)logline('sent queued item \u2192 '+sel,'ok');
    else logline('send blocked: '+d.error,'err');
    renderQueue(d.drafts||[]);loadSessions();
  }catch(e){logline('send error: '+e.message,'err');}
}
async function moveDraft(id,dir){const d=await api('/api/drafts/move',{session:sel,id,dir});renderQueue(d.drafts||[]);}
async function delDraft(id){const d=await api('/api/drafts/delete',{session:sel,id});renderQueue(d.drafts||[]);loadSessions();}
async function sendAll(){
  if(!sel)return;
  try{
    const d=await api('/api/drafts/send_all',{session:sel,submit:$('#submit').checked});
    if(d.ok)logline('send-all \u2192 '+sel+': sent '+d.sent+(d.errors&&d.errors.length?(' ('+d.errors.join('; ')+')'):''),d.errors&&d.errors.length?'err':'ok');
    else logline('send-all blocked: '+d.error,'err');
    renderQueue(d.drafts||[]);loadSessions();
  }catch(e){logline('send-all error: '+e.message,'err');}
}
async function toggleAuto(){
  if(!sel)return;
  const on=$('#autoflush').checked;
  const d=await api('/api/autoflush',{session:sel,enabled:on});
  logline('auto-send '+(d.autoflush?'ON':'OFF')+' for '+sel,'ok');
  loadSessions();
}

$('#sendNow').onclick=sendNow;$('#addQ').onclick=addToQueue;
async function sendKey(key,label){
  if(!sel){logline('pick a session first','err');return;}
  try{
    const d=await api('/api/key',{target:sel,key});
    if(d.ok)logline('sent '+(label||key)+' \\u2192 '+sel,'ok');
    else logline('key failed: '+d.error,'err');
  }catch(e){logline('key error: '+e.message,'err');}
  if($('#keep').checked)msg.focus();
  loadSessions();
}
$('#ctrlC').onclick=()=>sendKey('C-c','Ctrl-C');
$('#escKey').onclick=()=>sendKey('Escape','Esc');
$('#clear').onclick=()=>{msg.value='';msg.focus();};
$('#sendAll').onclick=sendAll;
$('#autoflush').onchange=toggleAuto;
$('#rescan').onclick=loadSessions;
$('#bcastBtn').onclick=broadcast;
$('#bcastAll').onclick=()=>selectBcast('all');
$('#bcastReady').onclick=()=>selectBcast('ready');
$('#bcastNone').onclick=()=>{bcast.clear();renderNav();};
$('#togglePane').onclick=()=>{paneVisible=!paneVisible;updatePaneVisibility();};
$('#paneLines').value=String(paneLines);
$('#paneLines').onchange=()=>{paneLines=parseInt($('#paneLines').value,10)||1000;
  localStorage.setItem('vaakPaneLines',String(paneLines));loadPane();};
function applyPaneFont(){const pre=$('#pane');if(pre)pre.style.fontSize=paneFont+'px';}
$('#paneFont').value=String(paneFont);
$('#paneFont').onchange=()=>{paneFont=parseInt($('#paneFont').value,10)||12;
  localStorage.setItem('vaakPaneFont',String(paneFont));applyPaneFont();};
applyPaneFont();
$('#readyAlert').checked=localStorage.getItem('vaakReadyAlert')!=='0';
$('#readyAlert').onchange=()=>localStorage.setItem('vaakReadyAlert',$('#readyAlert').checked?'1':'0');
window.addEventListener('focus',stopTitleFlash);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)stopTitleFlash();});
msg.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&$('#enterSends').checked){e.preventDefault();sendNow();}
});
updatePaneVisibility();
loadSessions().then(()=>{loadDrafts();loadPane();});
setInterval(loadSessions,2500);
setInterval(loadDrafts,4000);
setInterval(loadPane,1500);
msg.focus();

/* --- QR (open on phone) --- */
(function(){
  var loaded=false;
  function ensureLib(cb){
    if(loaded||window.qrcode){loaded=true;return cb();}
    var s=document.createElement('script');s.src='/qrcode.js';
    s.onload=function(){loaded=true;cb();};
    s.onerror=function(){logline('QR lib failed to load','err');};
    document.head.appendChild(s);
  }
  function showQR(){
    ensureLib(function(){
      try{
        var url=location.href;
        var qr=window.qrcode(0,'M');qr.addData(url);qr.make();
        $('#qrImg').innerHTML=qr.createSvgTag({cellSize:5,margin:2});
        $('#qrUrl').textContent=url;
        $('#qrModal').classList.add('show');
      }catch(e){logline('QR error: '+e.message,'err');}
    });
  }
  $('#qrBtn').onclick=showQR;
  $('#qrClose').onclick=function(){$('#qrModal').classList.remove('show');};
  $('#qrModal').onclick=function(e){if(e.target===$('#qrModal'))$('#qrModal').classList.remove('show');};

/* ---- Model Guide ---- */
const MG_DATA=[
  {id:"allrounder",task:"Everyday coding, features & PRs (best default)",
   model:"Claude Sonnet 5",mid:"claude-sonnet-5",effort:"medium\u2013high",ctx:"default",
   why:"The best all-round workhorse: near-Opus quality at much higher speed and lower cost. Start here for almost everything.",
   alts:["claude-sonnet-4.6","claude-opus-4.8"]},
  {id:"hard",task:"Deep debugging, architecture & big refactors",
   model:"Claude Opus 5",mid:"claude-opus-5",effort:"high\u2013max",ctx:"default / long_context",
   why:"Strongest reasoning and multi-step tool use. Reach for it when correctness matters more than speed, or when Sonnet stalls on a hard problem.",
   alts:["claude-opus-4.8","claude-opus-4.7","gpt-5.6-sol"]},
  {id:"codegen",task:"Pure code generation & completions",
   model:"GPT-5.3-Codex",mid:"gpt-5.3-codex",effort:"medium\u2013high",ctx:"default",
   why:"Code-specialized model tuned for writing and completing code with minimal ceremony.",
   alts:["claude-sonnet-5","gpt-5.5"]},
  {id:"reasoning",task:"Planning, algorithms & structured reasoning",
   model:"GPT-5.6 Sol",mid:"gpt-5.6-sol",effort:"high\u2013xhigh",ctx:"default / long_context",
   why:"Excellent step-by-step reasoning for math, algorithms, and detailed implementation plans. Use xhigh effort for the hardest problems.",
   alts:["gpt-5.5","claude-opus-5","gemini-3.1-pro-preview"]},
  {id:"bigcontext",task:"Large-codebase understanding, long docs & multimodal",
   model:"Gemini 3.1 Pro",mid:"gemini-3.1-pro-preview",effort:"high",ctx:"long_context",
   why:"Huge context window plus strong reasoning and image/PDF input \u2014 ideal for whole-repo comprehension and long documents.",
   alts:["claude-opus-5","gpt-5.6-terra"]},
  {id:"fast",task:"Fast iteration, simple edits & high volume",
   model:"Claude Haiku 4.5",mid:"claude-haiku-4.5",effort:"low",ctx:"default",
   why:"Quick and cheap for small, well-scoped edits and high-throughput work where you don\u2019t need deep reasoning.",
   alts:["gemini-3.6-flash","gpt-5-mini"]},
  {id:"lookup",task:"Quick lookups & search / explore subagents",
   model:"MAI-Code-1-Flash",mid:"mai-code-1-flash-picker",effort:"low",ctx:"default",
   why:"Lightweight and fast \u2014 great to assign to explore/search subagents so they scan the codebase cheaply while your main model does the heavy lifting.",
   alts:["gemini-3.5-flash","gpt-5.4-mini"]},
];
const MG_RULES=[
  "Start on <b>Sonnet</b> (the default). Escalate to <b>Opus</b> only when it stalls or the task is genuinely hard.",
  "Raise <b>effort</b> before switching models \u2014 a high-effort Sonnet often beats a low-effort Opus.",
  "Use <b>long_context</b> for whole-repo or long-document tasks; use <b>Flash / mini</b> to keep routine work cheap.",
  "For <b>subagents</b>: give explore/search agents a Flash/mini model; give hard implementation agents Opus or Sonnet."
];
const MG_ICON={
  allrounder:'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
  hard:'<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
  codegen:'<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  reasoning:'<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  bigcontext:'<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  fast:'<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  lookup:'<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
};
function mgSvg(id){return '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" '+
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(MG_ICON[id]||'')+'</svg>';}

(function initModelGuide(){
  const rail=$('#mgRail'), modal=$('#mgModal');
  let selectedIdx=-1, typeahead='', typeTimer=null;
  MG_DATA.forEach((d,i)=>{
    const tile=document.createElement('button');
    tile.type='button';tile.className='mg-tile';tile.id='mgtile-'+i;tile.dataset.mgTile='true';
    tile.setAttribute('role','radio');tile.setAttribute('aria-checked','false');
    tile.innerHTML=`<span class="mg-icon">${mgSvg(d.id)}</span>
      <span class="mg-tt"><b>${d.task}</b><small>${d.model} \u00b7 ${d.effort} \u00b7 ${d.ctx}</small></span>
      <span class="mg-check" aria-hidden="true">\u2713</span>`;
    tile.addEventListener('click',()=>mgSelect(i,true));
    tile.addEventListener('keydown',e=>mgTileKey(e,i));
    rail.appendChild(tile);
  });
  MG_RULES.forEach((r,i)=>{
    const d=document.createElement('div');d.className='mg-rule';
    d.innerHTML=`<span class="n">${i+1}</span><p>${r}</p>`;
    $('#mgRules').appendChild(d);
  });
  MG_DATA.forEach(d=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${d.task}</td><td><b>${d.model}</b><br><span class="m">${d.mid}</span></td><td>${d.effort}</td><td>${d.ctx}</td>`;
    $('#mgTbody').appendChild(tr);
  });
  function mgFocus(i){const el=rail.children[Math.max(0,Math.min(MG_DATA.length-1,i))];if(el)el.focus();}
  function mgSelect(i,focus){
    selectedIdx=i;const d=MG_DATA[i];
    [...rail.children].forEach((tile,idx)=>{tile.setAttribute('aria-checked',idx===i?'true':'false');tile.tabIndex=idx===i?0:-1;});
    mgRender(d);if(focus)mgFocus(i);
  }
  function mgRender(d){
    $('#mgRTask').textContent='Recommended for: '+d.task;
    $('#mgRIcon').innerHTML=mgSvg(d.id);$('#mgRModel').textContent=d.model;
    $('#mgRBadges').innerHTML=
      `<span class="mg-badge"><span class="k">id</span> <span style="font-family:ui-monospace,monospace">${d.mid}</span></span>`+
      `<span class="mg-badge eff"><span class="k">effort</span> ${d.effort}</span>`+
      `<span class="mg-badge ctx"><span class="k">context</span> ${d.ctx}</span>`;
    $('#mgRWhy').innerHTML=d.why;
    $('#mgRAlts').innerHTML=d.alts.map(a=>`<span class="mg-chip">${a}</span>`).join('');
    const cmd='/model '+d.mid;$('#mgRCmd').textContent=cmd;
    const cp=$('#mgCopyBtn');cp.classList.remove('done');cp.textContent='Copy';
    cp.onclick=()=>mgCopy(cmd,cp);
    const lb=$('#mgLaunchBtn');
    if(lb){
      lb.classList.remove('done');lb.disabled=false;lb.textContent='🚀 Launch session';
      lb.onclick=()=>mgLaunch(d,lb);
    }
  }
  async function mgLaunch(d,btn){
    const suggested=d.id.replace(/[^A-Za-z0-9._-]/g,'')||'copilot';
    const name=(prompt(`New tmux session name for the "${d.model}" copilot?\n\nThis will:\n  1. tmux new -d -s <name>\n  2. run cli-copilot inside it\n  3. send /allow-all\n  4. select model ${d.mid}`, suggested)||'').trim();
    if(!name)return;
    btn.disabled=true;const orig=btn.textContent;btn.textContent='Launching\u2026';
    try{
      const r=await api('/api/spawn_session',{session:name,model_id:d.mid});
      if(r&&r.ok){
        btn.classList.add('done');btn.textContent='\u2713 Launched: '+name;
        toast(`Launched session ${name} with ${d.model}`);
        logline('spawned '+name+' model='+d.mid,'ok');
        loadSessions();
        setTimeout(()=>{btn.classList.remove('done');btn.disabled=false;btn.textContent=orig;},4000);
      } else {
        btn.disabled=false;btn.textContent=orig;
        toast('Launch failed: '+((r&&r.error)||'unknown'));
      }
    }catch(err){
      btn.disabled=false;btn.textContent=orig;
      toast('Launch error: '+err.message);
    }
  }
  function mgCopy(text,b){
    const done=()=>{b.classList.add('done');b.textContent='Copied \u2713';setTimeout(()=>{b.classList.remove('done');b.textContent='Copy';},1600);};
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(done,()=>fb(text,done));}else fb(text,done);
  }
  function fb(text,done){const ta=document.createElement('textarea');ta.value=text;ta.style.cssText='position:fixed;opacity:0';document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done();}catch(e){}document.body.removeChild(ta);}
  function mgTileKey(e,i){
    let next=null;
    if(e.key==='ArrowDown'||e.key==='ArrowRight')next=(i+1)%MG_DATA.length;
    else if(e.key==='ArrowUp'||e.key==='ArrowLeft')next=(i+MG_DATA.length-1)%MG_DATA.length;
    else if(e.key==='Home')next=0;
    else if(e.key==='End')next=MG_DATA.length-1;
    if(next!==null){e.preventDefault();mgSelect(next,true);}
    else if(e.key==='Enter'||e.key===' '){e.preventDefault();mgSelect(i,true);}
    else if(e.key==='Escape'){e.preventDefault();mgHide();}
    else if(e.key.length===1&&/\S/.test(e.key)){
      clearTimeout(typeTimer);typeahead+=e.key.toLowerCase();
      const idx=MG_DATA.findIndex(d=>d.task.toLowerCase().startsWith(typeahead));
      if(idx>=0)mgSelect(idx,true);typeTimer=setTimeout(()=>typeahead='',600);
    }
  }
  function mgShow(){modal.classList.add('show');mgSelect(0,false);setTimeout(()=>mgFocus(0),0);}
  function mgHide(){modal.classList.remove('show');$('#mgBtn').focus();}
  $('#mgTabToggle').addEventListener('click',()=>{
    const t=$('#mgTable'),b=$('#mgTabToggle'),show=!t.classList.contains('show');
    t.classList.toggle('show',show);b.textContent=show?'Hide table':'Show table';b.setAttribute('aria-expanded',show?'true':'false');
  });
  $('#mgBtn').onclick=mgShow;
  $('#mgClose').onclick=mgHide;
  modal.onclick=e=>{if(e.target===modal)mgHide();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modal.classList.contains('show'))mgHide();});
  mgSelect(0,false);
})();
})();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Vaak/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        if p in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif p == "/api/info":
            self._json({"default": TARGET,
                        "default_id": resolve_pane_id(TARGET),
                        "target_ok": target_exists(TARGET),
                        "targets": list_targets()})
        elif p == "/api/sessions":
            # Nav bar payload: sessions with status + queued-draft counts.
            sessions = list_sessions()
            for s in sessions:
                s["drafts"] = len(get_drafts(s["name"]))
                s["autoflush"] = s["name"] in _autoflush
            self._json({"default": TARGET, "sessions": sessions})
        elif p == "/api/status":
            target = (q.get("target", [TARGET])[0]) or TARGET
            self._json({"target": target, "status": session_status(target)})
        elif p == "/api/pane":
            target = (q.get("target", [TARGET])[0]) or TARGET
            ok, data = capture_pane_lines(target, q.get("lines", [None])[0])
            if ok:
                self._json({"target": target, "lines": data})
            else:
                self._json({"target": target, "lines": [], "error": data}, 404)
        elif p == "/api/drafts":
            session = (q.get("session", [""])[0])
            self._json({"session": session, "drafts": get_drafts(session),
                        "autoflush": session in _autoflush})
        elif p == "/api/health":
            self._json({"ok": True})
        elif p == "/qrcode.js":
            try:
                js = (Path(__file__).resolve().parent / "qrcode.js").read_bytes()
                self._send(200, js, "application/javascript; charset=utf-8")
            except OSError:
                self._send(404, b"// qrcode.js not vendored", "application/javascript")
        elif p == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"ok": False, "error": "bad request"}, 400)
            return
        if not secrets.compare_digest(str(body.get("token", "")), TOKEN):
            self._json({"ok": False, "error": "invalid token"}, 403)
            return

        if p == "/api/send":
            target = str(body.get("target") or TARGET)
            ok, info = inject(target, str(body.get("text", "")),
                              bool(body.get("submit", True)))
            self._json({"ok": ok, "injected": info} if ok
                       else {"ok": False, "error": info})
            return

        if p == "/api/broadcast":
            results = broadcast_send(body.get("targets") or [],
                                     str(body.get("text", "")),
                                     bool(body.get("submit", True)))
            self._json({"ok": True, "count": len(results),
                        "sent": sum(1 for r in results if r["ok"]),
                        "results": results})
            return

        if p == "/api/key":
            target = str(body.get("target") or TARGET)
            ok, info = send_key(target, str(body.get("key", "")))
            self._json({"ok": ok, "key": info, "target": target} if ok
                       else {"ok": False, "error": info})
            return

        if p == "/api/kill_session":
            name = str(body.get("session") or body.get("target") or "").strip()
            ok, info = kill_session(name)
            self._json({"ok": ok, "session": info} if ok
                       else {"ok": False, "error": info})
            return

        if p == "/api/spawn_session":
            name = str(body.get("session") or body.get("name") or "").strip()
            model_id = str(body.get("model_id") or body.get("mid") or "").strip()
            ok, info = spawn_copilot_session(name, model_id)
            self._json({"ok": ok, "session": info, "model_id": model_id} if ok
                       else {"ok": False, "error": info})
            return

        # --- draft queue endpoints (all keyed by session name) ---
        session = str(body.get("session") or "")
        submit = bool(body.get("submit", True))
        if p == "/api/drafts/add":
            text = str(body.get("text", "")).strip()
            if not session or not text:
                self._json({"ok": False, "error": "session and text required"}, 400)
                return
            item = add_draft(session, text)
            self._json({"ok": True, "item": item, "drafts": get_drafts(session)})
        elif p == "/api/drafts/update":
            ok = update_draft(session, str(body.get("id", "")),
                              str(body.get("text", "")))
            self._json({"ok": ok, "drafts": get_drafts(session)})
        elif p == "/api/drafts/delete":
            ok = delete_draft(session, str(body.get("id", "")))
            self._json({"ok": ok, "drafts": get_drafts(session)})
        elif p == "/api/drafts/move":
            ok = move_draft(session, str(body.get("id", "")),
                            str(body.get("dir", "up")))
            self._json({"ok": ok, "drafts": get_drafts(session)})
        elif p == "/api/drafts/send":
            ok, info = send_draft(session, str(body.get("id", "")), submit)
            self._json({"ok": ok, "info": info, "drafts": get_drafts(session)}
                       if ok else {"ok": False, "error": info,
                                   "drafts": get_drafts(session)})
        elif p == "/api/drafts/send_all":
            st = session_status(session)
            if st != "ready":
                self._json({"ok": False, "error": f"session is {st}",
                            "drafts": get_drafts(session)})
                return
            sent, errors = send_all_drafts(session, submit)
            self._json({"ok": True, "sent": sent, "errors": errors,
                        "drafts": get_drafts(session)})
        elif p == "/api/autoflush":
            on = bool(body.get("enabled", False))
            if not session:
                self._json({"ok": False, "error": "session required"}, 400)
                return
            with _drafts_lock:
                if on:
                    _autoflush.add(session)
                else:
                    _autoflush.discard(session)
                _save_autoflush()
            self._json({"ok": True, "session": session, "autoflush": on})
        else:
            self._json({"error": "not found"}, 404)


def main():
    ip = _host_ip()
    url = f"http://{ip}:{PORT}/?token={TOKEN}"
    tgt_status = "found" if target_exists(TARGET) else f"NOT found — start: tmux new -s {TARGET}"
    print("Vaak bridge ready.")
    print(f"  tmux target : {TARGET}  ({tgt_status})")
    print(f"  drafts file : {DRAFTS_PATH}")
    print(f"  autoflush   : {AUTOFLUSH_PATH} (persisted across restarts)")
    print(f"  open in browser (laptop): {url}")
    print(f"  local: http://localhost:{PORT}/?token={TOKEN}")
    start_autoflush()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
