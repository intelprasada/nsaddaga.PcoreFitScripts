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


def test_kill_session_rejects_empty():
    ok, msg = D.kill_session("")
    assert ok is False and "required" in msg


def test_kill_session_rejects_shell_metachars():
    for bad in ["foo;rm -rf x", "a|b", "a`whoami`", "a$b", "a b\nc", "a\"b", "a'b", "a\\b"]:
        ok, msg = D.kill_session(bad)
        assert ok is False, f"expected reject for {bad!r}"
        assert "invalid characters" in msg, f"got: {msg}"


def test_kill_session_rejects_missing():
    ok, msg = D.kill_session("vaak_no_such_%s" % uuid.uuid4().hex[:8])
    assert ok is False and "not found" in msg


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_kill_session_kills_real_session_and_purges_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json", raising=False)
    sess = "vaak_kill_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    try:
        D.add_draft(sess, "queued item 1")
        assert D.get_drafts(sess), "sanity: draft should exist before kill"
        ok, name = D.kill_session(sess)
        assert ok is True and name == sess
        r = subprocess.run(["tmux", "has-session", "-t", sess],
                           capture_output=True, text=True, check=False)
        assert r.returncode != 0
        assert D.get_drafts(sess) == []
    finally:
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)


# ---------------------------------------------------------------------------
# Auto-flush persistence (PR #360)
# ---------------------------------------------------------------------------
@pytest.fixture()
def autoflush_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "AUTOFLUSH_PATH", tmp_path / "autoflush.json")
    # snapshot & restore the module-level set so tests don't leak state
    saved = set(D._autoflush)
    D._autoflush.clear()
    yield D.AUTOFLUSH_PATH
    D._autoflush.clear()
    D._autoflush.update(saved)


def test_autoflush_load_missing_file_returns_empty_set(autoflush_tmp):
    assert D._load_autoflush() == set()


def test_autoflush_load_handles_malformed_file(autoflush_tmp):
    autoflush_tmp.write_text("{not valid json", encoding="utf-8")
    assert D._load_autoflush() == set()


def test_autoflush_save_and_load_list_form(autoflush_tmp):
    D._autoflush.update({"alpha", "beta"})
    D._save_autoflush()
    assert autoflush_tmp.is_file()
    on_disk = json.loads(autoflush_tmp.read_text(encoding="utf-8"))
    assert sorted(on_disk) == ["alpha", "beta"]
    assert D._load_autoflush() == {"alpha", "beta"}


def test_autoflush_load_accepts_dict_form(autoflush_tmp):
    autoflush_tmp.write_text(
        json.dumps({"sessions": ["one", "two", 42]}), encoding="utf-8"
    )
    # ints/other garbage are filtered out
    assert D._load_autoflush() == {"one", "two"}


def test_autoflush_save_is_atomic(autoflush_tmp):
    D._autoflush.add("s")
    D._save_autoflush()
    # no leftover .tmp
    assert not autoflush_tmp.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# kill_session validation (PR #354) — pure-logic guards, no tmux needed
# ---------------------------------------------------------------------------
def test_kill_session_rejects_empty_name():
    ok, msg = D.kill_session("")
    assert ok is False
    assert "required" in msg


def test_kill_session_rejects_whitespace_only():
    ok, msg = D.kill_session("   \t  ")
    assert ok is False
    assert "required" in msg


@pytest.mark.parametrize("bad", [
    "foo;rm", "foo|bar", "foo&bar", "foo`whoami`", "foo$X",
    "foo<x", "foo>x", 'foo"x', "foo'x", "foo\\x", "foo\nbar",
])
def test_kill_session_rejects_shell_metacharacters(bad):
    ok, msg = D.kill_session(bad)
    assert ok is False
    assert "invalid characters" in msg


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_kill_session_reports_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    monkeypatch.setattr(D, "AUTOFLUSH_PATH", tmp_path / "autoflush.json")
    name = "vaak_no_such_%s" % uuid.uuid4().hex[:8]
    ok, msg = D.kill_session(name)
    assert ok is False
    assert "not found" in msg


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_kill_session_purges_drafts_and_autoflush(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    monkeypatch.setattr(D, "AUTOFLUSH_PATH", tmp_path / "autoflush.json")
    # start clean autoflush set for the assertion
    saved_af = set(D._autoflush)
    D._autoflush.clear()
    sess = "vaak_kill_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    created = True
    try:
        D.add_draft(sess, "will-be-purged")
        D._autoflush.add(sess)
        assert D.get_drafts(sess), "precondition: draft was queued"
        ok, name = D.kill_session(sess)
        created = False
        assert ok is True and name == sess
        # session gone
        r = subprocess.run(["tmux", "has-session", "-t", sess],
                           capture_output=True, check=False)
        assert r.returncode != 0
        # drafts + autoflush entry cleared
        assert D.get_drafts(sess) == []
        assert sess not in D._autoflush
    finally:
        if created:
            subprocess.run(["tmux", "kill-session", "-t", sess], check=False)
        D._autoflush.clear()
        D._autoflush.update(saved_af)


# ---------------------------------------------------------------------------
# spawn_copilot_session validation (PR #356) — pure-logic guards
# ---------------------------------------------------------------------------
def test_spawn_rejects_empty_name():
    ok, msg = D.spawn_copilot_session("", "claude-sonnet-5")
    assert ok is False
    assert "required" in msg


@pytest.mark.parametrize("bad", [
    ".dotstart",           # must start alphanumeric
    "-dashstart",          # must start alphanumeric
    "foo;rm",              # shell metachar
    "foo|bar",
    "foo$X",
    "foo\nbar",
    "a" * 65,              # too long
])
def test_spawn_rejects_bad_name(bad):
    ok, msg = D.spawn_copilot_session(bad, "")
    assert ok is False
    assert "invalid session name" in msg


@pytest.mark.parametrize("good", [
    "simple", "with_underscore-and.dot", "9numeric", "Zed",
    "With Space", "a" * 64,
])
def test_spawn_name_validation_accepts_good(good, monkeypatch):
    # Stop before we actually shell out: if target_exists returns True the
    # function returns the "already exists" error, proving it survived
    # every validation check.
    monkeypatch.setattr(D, "target_exists", lambda _n: True)
    ok, msg = D.spawn_copilot_session(good, "")
    assert ok is False
    assert "already exists" in msg


@pytest.mark.parametrize("bad_model", [
    "-startsdash", ".startsdot", "foo bar", "foo;bar", "foo/bar",
    "foo|bar", "foo\nbar", "foo$X",
])
def test_spawn_rejects_bad_model_id(bad_model, monkeypatch):
    # Get past the name & existence checks — the model check comes last.
    monkeypatch.setattr(D, "target_exists", lambda _n: False)
    ok, msg = D.spawn_copilot_session("cleanname", bad_model)
    # If model check bails out we get "invalid model id"; if it doesn't
    # we'd get past to `tmux new` — which the model regex must prevent.
    if ok:
        pytest.fail("bad model id %r slipped past validation" % bad_model)
    assert "invalid model id" in msg


# ---------------------------------------------------------------------------
# Prompt history (PR #364)
# ---------------------------------------------------------------------------
@pytest.fixture()
def history_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "HISTORY_PATH", tmp_path / "history.json")
    # snapshot & clear so tests don't see each other's writes
    with D._history_lock:
        saved = list(D._history)
        D._history.clear()
    yield D.HISTORY_PATH
    with D._history_lock:
        D._history.clear()
        for it in saved:
            D._history.append(it)


def test_history_record_appends_and_persists(history_tmp):
    D._record_history("s1", "hello", submit=True, source="send")
    assert len(D._history) == 1
    it = D._history[-1]
    assert it["session"] == "s1"
    assert it["text"] == "hello"
    assert it["submit"] is True
    assert it["source"] == "send"
    assert "id" in it and "ts" in it
    # persisted
    assert history_tmp.is_file()
    on_disk = json.loads(history_tmp.read_text(encoding="utf-8"))
    assert on_disk["items"][0]["text"] == "hello"


def test_history_record_ignores_empty_text(history_tmp):
    D._record_history("s1", "", submit=True)
    D._record_history("s1", "   \t  \n", submit=True)
    assert len(D._history) == 0


def test_history_reload_from_disk(history_tmp):
    D._record_history("s1", "one", submit=True)
    D._record_history("s2", "two", submit=False, source="broadcast")
    reloaded = D._load_history()
    assert len(reloaded) == 2
    assert reloaded[0]["text"] == "one"
    assert reloaded[1]["session"] == "s2"
    assert reloaded[1]["submit"] is False
    assert reloaded[1]["source"] == "broadcast"


def test_history_deque_bounded_by_history_max(history_tmp):
    # The module-level deque is created with maxlen=HISTORY_MAX. Push a few
    # more than the cap and confirm the oldest entries fall off.
    max_n = D._history.maxlen
    assert max_n and max_n >= 10
    for i in range(max_n + 5):
        D._record_history("cap", "prompt-%d" % i, submit=True)
    assert len(D._history) == max_n
    # oldest 5 dropped
    texts = [it["text"] for it in D._history]
    assert "prompt-0" not in texts
    assert "prompt-4" not in texts
    assert texts[0] == "prompt-5"
    assert texts[-1] == "prompt-%d" % (max_n + 4)


def test_history_load_malformed_returns_empty(history_tmp):
    history_tmp.write_text("{not json", encoding="utf-8")
    assert D._load_history() == []


def test_history_load_handles_bare_list_form(history_tmp):
    # Older/alternative on-disk form is a bare list.
    payload = [{"session": "s", "text": "legacy", "ts": time.time(),
                "submit": True, "source": "send", "id": "h1"}]
    history_tmp.write_text(json.dumps(payload), encoding="utf-8")
    loaded = D._load_history()
    assert len(loaded) == 1 and loaded[0]["text"] == "legacy"


def test_history_api_get_and_filter(history_tmp):
    D._record_history("alpha", "one", submit=True)
    D._record_history("beta", "two", submit=True)
    D._record_history("alpha", "three", submit=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), D.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:%d/api/history?limit=10" % server.server_port
        with urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["total"] == 3
        # newest first
        assert [it["text"] for it in data["items"]] == ["three", "two", "one"]
        assert data["max"] == D.HISTORY_MAX
        # session filter
        url2 = "http://127.0.0.1:%d/api/history?session=alpha&limit=10" % (
            server.server_port)
        with urlopen(url2, timeout=5) as resp:
            data2 = json.loads(resp.read().decode("utf-8"))
        assert [it["text"] for it in data2["items"]] == ["three", "one"]
        # limit clamped
        url3 = "http://127.0.0.1:%d/api/history?limit=2" % server.server_port
        with urlopen(url3, timeout=5) as resp:
            data3 = json.loads(resp.read().decode("utf-8"))
        assert len(data3["items"]) == 2
        assert data3["limit"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_api_clear_wipes_state(history_tmp):
    D._record_history("s", "keep-a", submit=True)
    D._record_history("s", "keep-b", submit=True)
    assert len(D._history) == 2
    server = ThreadingHTTPServer(("127.0.0.1", 0), D.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/history/clear" % server.server_port,
            data=json.dumps({"token": D.TOKEN}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert len(D._history) == 0
        on_disk = json.loads(history_tmp.read_text(encoding="utf-8"))
        assert on_disk["items"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_api_clear_rejects_bad_token(history_tmp):
    server = ThreadingHTTPServer(("127.0.0.1", 0), D.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/history/clear" % server.server_port,
            data=json.dumps({"token": "not-the-token"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=5)
        assert excinfo.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_history_recorded_by_inject_on_real_tmux(tmp_path, monkeypatch,
                                                 history_tmp):
    monkeypatch.setattr(D, "DRAFTS_PATH", tmp_path / "drafts.json")
    sess = "vaak_hist_%s" % uuid.uuid4().hex[:8]
    subprocess.run(["tmux", "new", "-d", "-s", sess], check=True)
    try:
        marker = "VAAK_H_%s" % uuid.uuid4().hex[:6]
        ok, _ = D.inject(sess, "echo %s" % marker, submit=True)
        assert ok
        assert len(D._history) == 1
        it = D._history[-1]
        assert it["session"] == sess
        assert marker in it["text"]
        assert it["submit"] is True
    finally:
        subprocess.run(["tmux", "kill-session", "-t", sess], check=False)


def test_page_has_history_controls():
    # UI wiring for the History modal must be present in the served page.
    for needle in ('id="histBtn"', 'id="histModal"', 'id="histLimit"',
                   'id="histRefresh"', 'id="histClear"', 'id="histOnlySel"',
                   'id="histSearch"', '/api/history'):
        assert needle in D.PAGE


def test_page_has_attach_command_ui():
    # UI wiring for the copiable "tmux attach -t <name>" affordance (PR #362).
    assert 'id="attachCmd"' in D.PAGE
    assert 'class="attach"' in D.PAGE
    assert 'Copy attach command' in D.PAGE


def test_page_preserves_edit_state_across_render():
    """Regression: renderQueue used to blow away the in-flight edit box on
    every 4s loadDrafts poll. The fix captures & restores editing state."""
    assert "_captureEditState" in D.PAGE
    assert "_restoreEditState" in D.PAGE
    # Must be wired into renderQueue, not just defined.
    idx = D.PAGE.find("function renderQueue(")
    assert idx > 0
    end = D.PAGE.find("function beginEdit(", idx)
    assert end > idx
    body = D.PAGE[idx:end]
    assert "_captureEditState()" in body
    assert "_restoreEditState(" in body


def test_page_save_edit_closes_edit_box():
    """Regression: on save (or Enter), saveEdit must clear the .editing
    class BEFORE renderQueue re-runs, otherwise the capture/restore
    machinery introduced for the poll-wipe fix will silently reopen the
    edit box on the very next re-render."""
    idx = D.PAGE.find("async function saveEdit(")
    assert idx > 0
    end = D.PAGE.find("async function sendNow(", idx)
    assert end > idx
    body = D.PAGE[idx:end]
    # editing class dropped BEFORE renderQueue
    rm = body.find("row.classList.remove('editing')")
    rq = body.find("renderQueue(")
    assert rm > 0 and rq > 0 and rm < rq, "must clear .editing before renderQueue"


def test_page_has_compose_resize_gutter():
    """Compose area (status bar + msg textarea + controls) above the
    terminal mirror should be draggable via #gutterCompose, driven by
    the --compose-h CSS custom property, persisted in localStorage."""
    p = D.PAGE
    assert 'id="compose"' in p
    assert 'id="gutterCompose"' in p
    assert '--compose-h' in p or 'compose-h' in p
    assert 'vaakComposeH' in p
    # Gutter must appear AFTER #compose and BEFORE #paneWrap in DOM order.
    ic = p.find('id="compose"')
    ig = p.find('id="gutterCompose"')
    ip = p.find('id="paneWrap"')
    assert 0 < ic < ig < ip
    # Drag handler must be wired.
    assert "drag($('#gutterCompose'),'compose-h','y')" in p
