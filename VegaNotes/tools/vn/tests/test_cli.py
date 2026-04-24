"""Tests for the vn CLI. The HTTP client is monkey-patched so no server
is required."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest import mock

import pytest

from vn import cli, config


@pytest.fixture(autouse=True)
def _fake_creds(monkeypatch):
    monkeypatch.setattr(
        cli, "load_credentials",
        lambda profile=None: config.Credentials(
            url="http://x", user="u", password="p"
        ),
    )


class FakeClient:
    """Records calls; returns canned responses."""

    def __init__(self, *_, **__):
        self.calls: list[tuple] = []
        self.responses: dict[tuple[str, str], Any] = {}

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        return self.responses.get((method, path), {"ok": True})

    def get(self, path, **params):
        return self.request("GET", path, params=params)

    def patch(self, path, body):
        return self.request("PATCH", path, body=body)

    def put(self, path, body):
        return self.request("PUT", path, body=body)


@pytest.fixture
def fake_client(monkeypatch):
    fc = FakeClient()
    monkeypatch.setattr(cli, "Client", lambda *a, **kw: fc)
    return fc


# ---------- _parse_kv ------------------------------------------------------

def test_parse_kv_simple():
    body = cli._parse_kv(["status=done", "priority=P1", "eta=2026-W18"])
    assert body == {"status": "done", "priority": "P1", "eta": "2026-W18"}


def test_parse_kv_lists():
    body = cli._parse_kv(["owners=alice,bob", "feature=login,auth"])
    assert body == {"owners": ["alice", "bob"], "features": ["login", "auth"]}


def test_parse_kv_unknown_key():
    with pytest.raises(SystemExit):
        cli._parse_kv(["bogus=x"])


def test_parse_kv_missing_equals():
    with pytest.raises(SystemExit):
        cli._parse_kv(["statusdone"])


def test_parse_kv_add_note_alias():
    body = cli._parse_kv(["add-note=shipped"])
    assert body == {"add_note": "shipped"}


# ---------- task -----------------------------------------------------------

def test_task_patch(fake_client, capsys):
    fake_client.responses[("PATCH", "/api/tasks/T-123")] = {
        "id": 1, "task_uuid": "T-123", "status": "done", "title": "do it",
        "owners": ["alice"], "attrs": {"priority": "P1", "eta": "2026-W18"},
    }
    rc = cli.main(["task", "T-123", "status=done", "priority=P1", "eta=2026-W18"])
    assert rc == 0
    method, path, _, body = fake_client.calls[-1]
    assert method == "PATCH" and path == "/api/tasks/T-123"
    assert body == {"status": "done", "priority": "P1", "eta": "2026-W18"}
    out = capsys.readouterr().out
    assert "T-123" in out and "done" in out


def test_task_no_attrs_does_get(fake_client):
    fake_client.responses[("GET", "/api/tasks/T-9")] = {"task_uuid": "T-9", "title": "x"}
    cli.main(["task", "T-9"])
    method, path, _, _ = fake_client.calls[-1]
    assert method == "GET" and path == "/api/tasks/T-9"


def test_list_uses_task_uuid_in_id_column(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {
        "tasks": [
            {"id": 7, "task_uuid": "T-ABCDEF", "title": "stamped",
             "status": "wip", "owners": [], "attrs": {}},
            {"id": 8, "task_uuid": None, "title": "unstamped",
             "status": "todo", "owners": [], "attrs": {}},
        ]
    }
    cli.main(["list"])
    out = capsys.readouterr().out
    # Stamped row uses T-XXXXXX, unstamped falls back to int PK.
    assert "T-ABCDEF" in out
    assert "\n8 " in out or "\n8\t" in out or " 8 " in out


# ---------- list -----------------------------------------------------------

def test_list_filters_passed_through(fake_client):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main(["list", "--owner", "kushwanth", "--status", "open", "--hide-done"])
    method, path, params, _ = fake_client.calls[-1]
    assert method == "GET" and path == "/api/tasks"
    assert params["owner"] == "kushwanth"
    assert params["status"] == "open"
    assert params["hide_done"] is True


def test_list_json_output(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [{"id": 1}]}
    cli.main(["--json", "list"])
    out = capsys.readouterr().out
    assert json.loads(out) == {"tasks": [{"id": 1}]}


# ---------- note new -------------------------------------------------------

def test_note_new_default_path(fake_client, capsys):
    fake_client.responses[("PUT", "/api/notes")] = {"id": 42, "path": "ww16/standup-notes.md"}
    rc = cli.main(["note", "new", "--project", "ww16", "--title", "Standup Notes!"])
    assert rc == 0
    method, path, _, body = fake_client.calls[-1]
    assert method == "PUT" and path == "/api/notes"
    assert body["path"] == "ww16/standup-notes.md"
    assert body["body_md"].startswith("# Standup Notes!")
    out = capsys.readouterr().out
    assert "id=42" in out


def test_note_new_explicit_path_and_body(fake_client):
    fake_client.responses[("PUT", "/api/notes")] = {"id": 7, "path": "x/y.md"}
    cli.main([
        "note", "new", "--project", "ww16", "--title", "T",
        "--path", "x/y.md", "--body", "hello",
    ])
    _, _, _, body = fake_client.calls[-1]
    assert body == {"path": "x/y.md", "body_md": "hello"}


# ---------- whoami ---------------------------------------------------------

def test_whoami(fake_client, capsys):
    fake_client.responses[("GET", "/api/me")] = {"name": "alice", "is_admin": True}
    cli.main(["whoami"])
    out = capsys.readouterr().out.strip()
    assert out == "alice (admin)"


# ---------- config ---------------------------------------------------------

def test_credentials_env(monkeypatch):
    monkeypatch.setenv("VEGANOTES_URL", "http://h:1/")
    monkeypatch.setenv("VEGANOTES_USER", "u")
    monkeypatch.setenv("VEGANOTES_PASS", "p")
    monkeypatch.setattr(config, "CREDENTIALS_PATH", config.Path("/no/such/file"))
    c = config.load_credentials()
    assert c.url == "http://h:1" and c.user == "u" and c.password == "p"


def test_credentials_missing(monkeypatch):
    for v in ("VEGANOTES_URL", "VEGANOTES_USER", "VEGANOTES_PASS", "VEGANOTES_PROFILE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(config, "CREDENTIALS_PATH", config.Path("/no/such/file"))
    with pytest.raises(config.CredentialsError):
        config.load_credentials()


def test_credentials_file(monkeypatch, tmp_path):
    cred = tmp_path / "credentials"
    cred.write_text("[default]\nurl=http://x\nuser=a\npassword=b\n")
    for v in ("VEGANOTES_URL", "VEGANOTES_USER", "VEGANOTES_PASS", "VEGANOTES_PROFILE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(config, "CREDENTIALS_PATH", cred)
    c = config.load_credentials()
    assert (c.url, c.user, c.password) == ("http://x", "a", "b")
