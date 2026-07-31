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
                _autoflush.discard(sess)
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
.qi .qt{flex:1;white-space:pre-wrap;word-break:break-word;font-size:14px;min-width:0}
.qi .qa{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.empty{color:#8b949e;font-style:italic;font-size:13px}
#paneWrap{border:1px solid #30363d;border-radius:10px;background:#06090f;overflow:hidden}
#paneHead{display:flex;align-items:center;gap:8px;justify-content:space-between;padding:7px 10px;
 border-bottom:1px solid #30363d;color:#8b949e;font-size:12px}
#pane{margin:0;padding:10px;max-height:34vh;overflow:auto;white-space:pre-wrap;word-break:break-word;
 font:12px/1.35 ui-monospace,SFMono-Regular,Consolas,Liberation Mono,monospace;color:#c9d1d9}
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
 <button class="sec mini" id="qrBtn" style="margin-left:auto" title="Open on your phone">QR</button></header>
<div id="qrModal"><div id="qrCard">
 <h3>Open Vaak on your phone</h3>
 <div id="qrImg"></div>
 <div id="qrUrl"></div>
 <div class="hint" style="margin-top:8px">Scan with your phone camera (same network / VPN).</div>
 <button class="sec" id="qrClose">Close</button>
</div></div>
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
    pre.scrollTop=nearBottom?pre.scrollHeight:prevTop;
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
      `<span class="badge ${s.drafts?'':'zero'}">${s.drafts}</span>`;
    const bx=d.querySelector('.bx');
    bx.onclick=(e)=>{e.stopPropagation();
      if(bx.checked)bcast.add(s.name); else bcast.delete(s.name); updateBcastBtn();};
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
    const d=document.createElement('div');d.className='qi';
    d.innerHTML=`<span class="qt">${esc(it.text)}</span>`+
      `<span class="qa">`+
      `<button class="sec mini" data-a="copy">Copy</button>`+
      `<button class="mini" data-a="send">Send</button>`+
      `<button class="sec mini" data-a="up" ${i===0?'disabled':''}>\u2191</button>`+
      `<button class="sec mini" data-a="down" ${i===items.length-1?'disabled':''}>\u2193</button>`+
      `<button class="sec mini" data-a="del">\u2715</button>`+
      `</span>`;
    d.querySelector('[data-a=copy]').onclick=()=>copyText(it.text);
    d.querySelector('[data-a=send]').onclick=()=>sendDraft(it.id);
    d.querySelector('[data-a=up]').onclick=()=>moveDraft(it.id,'up');
    d.querySelector('[data-a=down]').onclick=()=>moveDraft(it.id,'down');
    d.querySelector('[data-a=del]').onclick=()=>delDraft(it.id);
    el.appendChild(d);
  });
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
            if on:
                _autoflush.add(session)
            else:
                _autoflush.discard(session)
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
    print(f"  open in browser (laptop): {url}")
    print(f"  local: http://localhost:{PORT}/?token={TOKEN}")
    start_autoflush()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
