"""Unit tests for vaak_server pure-logic helpers.

Pure functions run anywhere. The tmux-backed paths are covered by a single
integration test that skips cleanly when tmux is unavailable.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

_MOD = Path(__file__).resolve().parents[1] / "vaak_server.py"
_spec = importlib.util.spec_from_file_location("vaak_server", _MOD)
D = importlib.util.module_from_spec(_spec)
sys.modules["vaak_server"] = D
_spec.loader.exec_module(D)

_HAS_TMUX = shutil.which("tmux") is not None


# --- prompt flattening ------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("hello world", "hello world"),
    ("line one\nline two", "line one line two"),
    ("crlf\r\nend", "crlf end"),
    ("  trim me  ", "trim me"),
    ("a\n\nb", "a  b"),
])
def test_flatten_prompt(raw, expected):
    assert D._flatten_prompt(raw) == expected


# --- inject guards (no tmux needed) ----------------------------------------
def test_inject_empty_text_rejected():
    ok, msg = D.inject("whatever", "   ", submit=False)
    assert ok is False
    assert msg == "empty text"


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_inject_missing_target_reports_error():
    ok, msg = D.inject("no_such_session_%s" % uuid.uuid4().hex[:8],
                       "echo hi", submit=False)
    assert ok is False
    assert "not found" in msg


# --- config defaults --------------------------------------------------------
def test_token_is_nonempty():
    assert isinstance(D.TOKEN, str) and len(D.TOKEN) >= 8


def test_page_has_expected_controls():
    # the served HTML must expose the pieces the JS/tests rely on
    for needle in ('id="msg"', 'id="sesslist"', 'id="sendNow"', 'id="addQ"',
                   'id="queue"', 'id="pane"', 'id="readyAlert"', '/api/send',
                   '/api/sessions', '/api/pane', '/api/drafts/add'):
        assert needle in D.PAGE


# --- busy/ready detection regex --------------------------------------------
@pytest.mark.parametrize("tail,expected_busy", [
    ("  ◉ Working · 1.6 KiB  esc interrupt        Claude Opus", True),
    ("● Working · 3.8 KiB esc interrupt", True),
    ("Thinking", True),
    ("❯", False),
    ("$ ready prompt here", False),
    ("just some source code line", False),
])
def test_busy_regex(tail, expected_busy):
    assert bool(D.BUSY_RE.search(tail)) is expected_busy


# --- draft store (isolated to a temp file) ---------------------------------
@pytest.fixture()
def drafts_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    return D.DRAFTS_PATH


def test_draft_crud_and_persistence(drafts_tmp):
    sess = "unit_sess"
    assert D.get_drafts(sess) == []
    a = D.add_draft(sess, "first")
    b = D.add_draft(sess, "second")
    assert [d["text"] for d in D.get_drafts(sess)] == ["first", "second"]
    # persisted to disk
    assert drafts_tmp.is_file()
    # update
    assert D.update_draft(sess, a["id"], "first-edited") is True
    assert D.get_drafts(sess)[0]["text"] == "first-edited"
    # reorder: move second up
    assert D.move_draft(sess, b["id"], "up") is True
    assert [d["text"] for d in D.get_drafts(sess)] == ["second", "first-edited"]
    # move first item up is a no-op (returns False)
    top_id = D.get_drafts(sess)[0]["id"]
    assert D.move_draft(sess, top_id, "up") is False
    # pop head
    head = D.pop_first_draft(sess)
    assert head["text"] == "second"
    assert len(D.get_drafts(sess)) == 1
    # delete remaining
    rem_id = D.get_drafts(sess)[0]["id"]
    assert D.delete_draft(sess, rem_id) is True
    assert D.get_drafts(sess) == []
    # delete missing -> False
    assert D.delete_draft(sess, "nope") is False


def test_drafts_reload_from_disk(drafts_tmp):
    D.add_draft("s1", "persisted")
    # simulate a fresh process reading the same file
    reloaded = D._load_drafts()
    assert reloaded["s1"][0]["text"] == "persisted"


# --- tmux round trip (skips without tmux) -----------------------------------
@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_inject_round_trip_into_real_tmux_pane():
    sess = "vaak_pytest_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    try:
        assert D.target_exists(sess) is True
        # resolve_pane_id returns a %N id for the session
        pane = D.resolve_pane_id(sess)
        assert pane.startswith("%")
        # list_targets includes our session with a label
        labels = [t["label"] for t in D.list_targets()]
        assert any(sess in lbl for lbl in labels)
        # list_sessions includes it with a ready status (fresh shell)
        names = {s["name"]: s for s in D.list_sessions()}
        assert sess in names
        assert names[sess]["status"] in ("ready", "busy")
        # a fresh shell should read as ready
        assert D.session_status(sess) == "ready"
        # inject a marker and confirm it lands in the pane buffer
        marker = "VAAK_RT_%s" % uuid.uuid4().hex[:6]
        ok, sent = D.inject(sess, "echo %s" % marker, submit=True)
        assert ok is True and marker in sent
        cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", sess],
                             capture_output=True, text=True, check=True)
        assert marker in cap.stdout
    finally:
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_send_draft_gated_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    sess = "vaak_pytest_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    try:
        marker = "VAAK_SD_%s" % uuid.uuid4().hex[:6]
        item = D.add_draft(sess, "echo %s" % marker)
        ok, info = D.send_draft(sess, item["id"], submit=True)
        assert ok is True
        # removed from queue after a successful send
        assert D.get_drafts(sess) == []
        cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", sess],
                             capture_output=True, text=True, check=True)
        assert marker in cap.stdout
    finally:
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_api_pane_returns_recent_tmux_output(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    sess = "vaak_pytest_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), D.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        marker = "VAAK_PANE_%s" % uuid.uuid4().hex[:6]
        subprocess.run(["tmux", "send-keys", "-t", sess, "-l", "--",
                        "echo %s" % marker], check=True)
        subprocess.run(["tmux", "send-keys", "-t", sess, "Enter"], check=True)
        for _ in range(20):
            cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", sess],
                                 capture_output=True, text=True, check=True)
            if marker in cap.stdout:
                break
            time.sleep(0.05)
        url = "http://127.0.0.1:%d/api/pane?target=%s&lines=20" % (
            server.server_port, sess)
        with urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["target"] == sess
        assert any(marker in line for line in data["lines"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_send_all_drafts_keeps_submitted_items_on_separate_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    monkeypatch.setattr(D, "SEND_SETTLE", 0.05)
    monkeypatch.setattr(D, "SEND_ACCEPT_TIMEOUT", 2.0)
    monkeypatch.setattr(D, "SEND_POLL_INTERVAL", 0.05)
    sess = "vaak_pytest_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    try:
        marker1 = "VAAK_Q1_%s" % uuid.uuid4().hex[:6]
        marker2 = "VAAK_Q2_%s" % uuid.uuid4().hex[:6]
        D.add_draft(sess, "echo %s" % marker1)
        D.add_draft(sess, "echo %s" % marker2)
        sent, errors = D.send_all_drafts(sess, submit=True)
        assert sent == 2
        assert errors == []
        assert D.get_drafts(sess) == []
        cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", sess],
                             capture_output=True, text=True, check=True)
        lines = cap.stdout.splitlines()
        line1 = next((ln for ln in lines if marker1 in ln), "")
        line2 = next((ln for ln in lines if marker2 in ln), "")
        assert line1 and line2
        assert line1 != line2
        assert not any(marker1 in ln and marker2 in ln for ln in lines)
    finally:
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_broadcast_send_to_multiple_sessions():
    a = "vaak_bc_a_%s" % uuid.uuid4().hex[:8]
    b = "vaak_bc_b_%s" % uuid.uuid4().hex[:8]
    for s in (a, b):
        subprocess.run(["tmux", "new", "-d", "-s", s], check=True)
    try:
        marker = "VAAK_BC_%s" % uuid.uuid4().hex[:6]
        results = D.broadcast_send([a, b], "echo %s" % marker, submit=True)
        assert len(results) == 2
        assert all(r["ok"] for r in results)
        assert {r["target"] for r in results} == {a, b}
        for s in (a, b):
            cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", s],
                                 capture_output=True, text=True, check=True)
            assert marker in cap.stdout
    finally:
        for s in (a, b):
            subprocess.run(["tmux", "kill-session", "-t", s], check=False)


def test_broadcast_send_empty_targets():
    assert D.broadcast_send([], "echo hi", submit=True) == []


def test_send_key_rejects_unknown():
    ok, msg = D.send_key("whatever", "rm -rf /")
    assert ok is False and "not allowed" in msg


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_send_key_ctrl_c_interrupts():
    sess = "vaak_key_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    try:
        # Block the shell on a long sleep, then Ctrl-C it. If the interrupt
        # works, the prompt is freed and a follow-up echo prints within our
        # short window; if it didn't, the echo would queue behind the 60s sleep.
        subprocess.run(["tmux", "send-keys", "-t", sess, "-l", "--", "sleep 60"],
                       check=True)
        subprocess.run(["tmux", "send-keys", "-t", sess, "Enter"], check=True)
        time.sleep(0.8)
        ok, tk = D.send_key(sess, "C-c")
        assert ok is True and tk == "C-c"
        time.sleep(0.5)
        marker = "VAAK_INT_%s" % uuid.uuid4().hex[:6]
        D.inject(sess, "echo %s" % marker, submit=True)
        time.sleep(0.8)
        cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", sess],
                             capture_output=True, text=True, check=True)
        # The marker's OUTPUT line (bare marker) proves the prompt was freed.
        assert any(ln.strip() == marker for ln in cap.stdout.splitlines())
    finally:
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)
