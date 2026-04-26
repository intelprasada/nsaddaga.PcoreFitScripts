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


def test_list_where_compiles_to_wire_params(fake_client):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main([
        "list",
        "-w", "owner=alice",
        "-w", "@area=fit-val",
        "-w", "eta>=ww18",
        "-w", "project!=internal",
        "--sort", "eta:desc",
    ])
    _, _, params, _ = fake_client.calls[-1]
    assert params["owner"] == "alice"
    assert params["not_project"] == "internal"
    assert params["eta_after"] == "ww18"
    assert params["attr"] == "area:eq:fit-val"
    assert params["sort"] == "eta:desc"


def test_list_multiple_attrs_become_repeated_param(fake_client):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main(["list", "-w", "@area=a", "-w", "@area=b", "-w", "@risk=high"])
    _, _, params, _ = fake_client.calls[-1]
    assert isinstance(params["attr"], list)
    assert "area:eq:a" in params["attr"]
    assert "area:eq:b" in params["attr"]
    assert "risk:eq:high" in params["attr"]


def test_query_alias_is_list(fake_client):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main(["query", "-w", "owner=alice"])
    _, path, params, _ = fake_client.calls[-1]
    assert path == "/api/tasks" and params["owner"] == "alice"


def test_list_bad_where_returns_2(fake_client, capsys):
    rc = cli.main(["list", "-w", "garbage"])
    assert rc == 2
    assert "bad --where" in capsys.readouterr().err


def test_list_format_csv(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {
        "tasks": [
            {"id": 1, "task_uuid": "T-001", "title": "Hi",
             "status": "todo", "owners": ["a"], "attrs": {"priority": "P1"}},
        ]
    }
    cli.main(["list", "--format", "csv", "--columns", "id,priority,title"])
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[0] == "id,priority,title"
    assert lines[1] == "T-001,P1,Hi"


def test_list_format_jsonl(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {
        "tasks": [{"id": 1, "title": "x"}, {"id": 2, "title": "y"}]
    }
    cli.main(["list", "--format", "jsonl"])
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["id"] == 1
    assert json.loads(out[1])["id"] == 2


def test_list_format_ids(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {
        "tasks": [
            {"task_uuid": "T-AAA", "id": 5},
            {"task_uuid": None, "id": 6},
        ]
    }
    cli.main(["list", "--format", "ids"])
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["T-AAA", "6"]


def test_list_columns_reorder(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {
        "tasks": [{"id": 1, "task_uuid": "T-X", "title": "t", "status": "wip",
                   "owners": [], "attrs": {"area": "fit-val"}}]
    }
    cli.main(["list", "--columns", "title,area,id"])
    out = capsys.readouterr().out
    header = out.splitlines()[0]
    # Title first, then arbitrary attr column from attrs, then id.
    assert header.split() == ["TITLE", "AREA", "ID"]
    assert "fit-val" in out


def test_list_group_by_buckets_table(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {
        "tasks": [
            {"id": 1, "task_uuid": "T-1", "title": "a", "status": "todo",
             "owners": [], "attrs": {"area": "fit-val"}},
            {"id": 2, "task_uuid": "T-2", "title": "b", "status": "todo",
             "owners": [], "attrs": {"area": "fit-val"}},
            {"id": 3, "task_uuid": "T-3", "title": "c", "status": "todo",
             "owners": [], "attrs": {"area": "infra"}},
        ]
    }
    cli.main(["list", "--group-by", "area"])
    out = capsys.readouterr().out
    assert "== area=fit-val  (2) ==" in out
    assert "== area=infra  (1) ==" in out


def test_list_json_output(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [{"id": 1}]}
    cli.main(["--json", "list"])
    out = capsys.readouterr().out
    assert json.loads(out) == {"tasks": [{"id": 1}]}


# ---------- task / subtask / AR relationships -----------------------------

def test_type_column_classifies_task_subtask_ar(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "top", "status": "todo",
         "kind": "task", "parent_task_id": None, "owners": [], "attrs": {}},
        {"id": 2, "task_uuid": "T-2", "title": "child", "status": "todo",
         "kind": "task", "parent_task_id": 1, "owners": [], "attrs": {}},
        {"id": 3, "task_uuid": "T-3", "title": "ar1", "status": "open",
         "kind": "ar", "parent_task_id": None, "owners": [], "attrs": {}},
    ]}
    cli.main(["list", "--columns", "id,type,title"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    # Header + separator + 3 data rows
    assert lines[0].split() == ["ID", "TYPE", "TITLE"]
    body = "\n".join(lines[2:])
    assert "T-1" in body and " task " in body
    assert "T-2" in body and " subtask " in body
    assert "T-3" in body and " ar " in body


def test_tree_flag_sets_include_children_and_kind(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main(["list", "--tree"])
    method, path, params, _ = fake_client.calls[0]
    assert method == "GET" and path == "/api/tasks"
    assert params.get("include_children") is True
    # --tree widens kind to task,ar when caller did not pass --kind.
    assert params.get("kind") == "task,ar"


def test_tree_flag_respects_explicit_kind(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main(["list", "--tree", "--kind", "task"])
    _, _, params, _ = fake_client.calls[0]
    assert params.get("kind") == "task"
    assert params.get("include_children") is True


def test_tree_renders_subtasks_indented_under_parents(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "Parent A", "status": "wip",
         "kind": "task", "parent_task_id": None, "owners": [], "attrs": {},
         "children": [
             {"id": 11, "task_uuid": "T-1a", "title": "child one",
              "status": "todo", "kind": "task"},
             {"id": 12, "task_uuid": "T-1b", "title": "child two",
              "status": "done", "kind": "task"},
         ]},
        {"id": 2, "task_uuid": "T-2", "title": "Lone AR",
         "status": "open", "kind": "ar", "parent_task_id": None,
         "owners": [], "attrs": {}, "children": []},
    ]}
    cli.main(["list", "--tree", "--columns", "id,type,title"])
    out = capsys.readouterr().out
    # Parent A row, two indented children, then the AR.
    assert "Parent A" in out
    assert "├─ child one" in out
    assert "└─ child two" in out
    assert "Lone AR" in out
    # Children classify as subtask, AR row classifies as ar.
    parent_idx = out.index("Parent A")
    child_idx = out.index("child one")
    ar_idx = out.index("Lone AR")
    assert parent_idx < child_idx < ar_idx
    assert "subtask" in out[child_idx - 30: child_idx]
    assert "ar" in out[ar_idx - 30: ar_idx]


def test_with_children_passes_param_without_flattening(fake_client, capsys):
    payload = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "p", "status": "wip",
         "kind": "task", "parent_task_id": None, "owners": [], "attrs": {},
         "children": [{"id": 11, "task_uuid": "T-1a", "title": "c",
                       "status": "todo", "kind": "task"}]},
    ]}
    fake_client.responses[("GET", "/api/tasks")] = payload
    cli.main(["--json", "list", "--with-children"])
    _, _, params, _ = fake_client.calls[0]
    assert params.get("include_children") is True
    out = capsys.readouterr().out
    parsed = json.loads(out)
    # JSON keeps the nested children intact (no flattening for json fmt).
    assert parsed["tasks"][0]["children"][0]["task_uuid"] == "T-1a"


def test_tree_json_keeps_nested_children(fake_client, capsys):
    payload = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "p", "status": "wip",
         "kind": "task", "parent_task_id": None, "owners": [], "attrs": {},
         "children": [{"id": 11, "task_uuid": "T-1a", "title": "c",
                       "status": "todo", "kind": "task"}]},
    ]}
    fake_client.responses[("GET", "/api/tasks")] = payload
    cli.main(["list", "--tree", "--format", "json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["tasks"][0]["children"][0]["task_uuid"] == "T-1a"


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
