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
        {"id": 4, "task_uuid": "T-4", "title": "nested ar", "status": "open",
         "kind": "ar", "parent_task_id": 1, "owners": [], "attrs": {}},
    ]}
    cli.main(["list", "--columns", "id,type,title"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["ID", "TYPE", "TITLE"]
    body = "\n".join(lines[2:])
    # Values are upper-cased; AR wins over SUBTASK even when nested.
    assert "T-1" in body and " TASK " in body
    assert "T-2" in body and " SUBTASK " in body
    assert "T-3" in body and " AR " in body
    # T-4: kind=ar AND parent set → still AR, not SUBTASK.
    assert "T-4" in body
    t4_line = next(ln for ln in lines if "T-4" in ln)
    assert " AR " in t4_line and "SUBTASK" not in t4_line


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
    assert "Parent A" in out
    assert "├─ child one" in out
    assert "└─ child two" in out
    assert "Lone AR" in out
    parent_idx = out.index("Parent A")
    child_idx = out.index("child one")
    ar_idx = out.index("Lone AR")
    assert parent_idx < child_idx < ar_idx
    # Type column is upper-case now.
    assert "SUBTASK" in out[child_idx - 30: child_idx]
    assert " AR " in out[ar_idx - 30: ar_idx]


def test_tree_dedupes_subtask_returned_at_top_level_and_as_child(fake_client, capsys):
    """Issue #100 case 1: API returns the subtask twice (top-level + child)
    when --tree widens kind to task,ar; flatten must drop the duplicate."""
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "Parent", "status": "wip",
         "kind": "task", "parent_task_id": None, "owners": ["aboli"],
         "attrs": {}, "children": [
             {"id": 11, "task_uuid": "T-1a", "title": "Subtask", "status": "todo",
              "kind": "task", "parent_task_id": 1, "eta": "2026-05-04",
              "eta_raw": "ww18.2"},
         ]},
        # Same subtask, also returned at top level because it matched
        # --owner=aboli and the kind=task,ar filter doesn't drop subtasks.
        {"id": 11, "task_uuid": "T-1a", "title": "Subtask", "status": "todo",
         "kind": "task", "parent_task_id": 1, "owners": ["aboli"],
         "attrs": {"eta": "ww18.2"}},
    ]}
    cli.main(["list", "--tree", "--owner", "aboli", "--columns", "id,type,title"])
    out = capsys.readouterr().out
    # T-1a appears exactly once, as a child of T-1.
    assert out.count("T-1a") == 1
    assert "├─ Subtask" in out or "└─ Subtask" in out


def test_tree_keeps_orphan_subtasks_at_top_level(fake_client, capsys):
    """If a subtask matches the filter but its parent does NOT appear in
    the result, keep it at the top level so it isn't lost."""
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        # Orphan: parent_task_id=99, but task id=99 not in this result.
        {"id": 11, "task_uuid": "T-1a", "title": "Orphan sub", "status": "todo",
         "kind": "task", "parent_task_id": 99, "owners": ["aboli"],
         "attrs": {"eta": "ww18.2"}},
    ]}
    cli.main(["list", "--tree", "--owner", "aboli", "--columns", "id,type,title"])
    out = capsys.readouterr().out
    assert "T-1a" in out
    assert "Orphan sub" in out


def test_tree_renders_child_owners_from_api_payload(fake_client, capsys):
    """Issue #104: subtask owners used to render blank because the API
    child shape lacked an owners field. With #104, owners arrives on the
    child and _task_field renders it normally."""
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "Parent", "status": "wip",
         "kind": "task", "parent_task_id": None, "owners": ["alice"],
         "attrs": {}, "children": [
             {"id": 11, "task_uuid": "T-1a", "title": "Sub", "status": "todo",
              "kind": "task", "parent_task_id": 1, "owners": ["bob"],
              "projects": ["proj"], "features": [],
              "eta": "2026-05-04", "eta_raw": "ww18.2"},
         ]},
    ]}
    cli.main(["list", "--tree", "--columns", "id,owners,title"])
    out = capsys.readouterr().out
    sub_line = next(ln for ln in out.splitlines() if "T-1a" in ln)
    assert "bob" in sub_line, f"expected bob in subtask row: {sub_line!r}"


def test_tree_child_eta_renders_raw_value_like_parent(fake_client, capsys):
    """Issue #100 case 2: child rows used to render normalized
    (yyyy-mm-dd) eta while parents rendered raw (wwNN); flatten now
    promotes eta_raw into a synthetic attrs map for parity."""
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        {"id": 1, "task_uuid": "T-1", "title": "Parent", "status": "wip",
         "kind": "task", "parent_task_id": None, "owners": [],
         "attrs": {"eta": "ww18.2"}, "children": [
             {"id": 11, "task_uuid": "T-1a", "title": "Sub", "status": "todo",
              "kind": "task", "parent_task_id": 1,
              "eta": "2026-05-04", "eta_raw": "ww18.2"},
         ]},
    ]}
    cli.main(["list", "--tree", "--columns", "id,eta,title"])
    out = capsys.readouterr().out
    # Both rows show the raw ww-format; the normalized form does not leak.
    assert out.count("ww18.2") == 2
    assert "2026-05-04" not in out


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


# ---------- vn show <resource> --------------------------------------------

def test_show_projects_table(fake_client, capsys):
    fake_client.responses[("GET", "/api/projects")] = [
        {"name": "ww17", "role": "manager"},
        {"name": "ww18", "role": "member"},
    ]
    rc = cli.main(["show", "projects"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ww17" in out and "manager" in out
    assert "ww18" in out and "member" in out


def test_show_projects_detail_combines_members_and_notes(fake_client, capsys):
    fake_client.responses[("GET", "/api/projects/ww18/members")] = [
        {"user_name": "alice", "role": "manager"},
    ]
    fake_client.responses[("GET", "/api/projects/ww18/notes")] = [
        {"id": 1, "path": "ww18/standup.md", "title": "Standup"},
    ]
    rc = cli.main(["show", "projects", "ww18"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "== project ww18 ==" in out
    assert "alice" in out and "manager" in out
    assert "ww18/standup.md" in out


def test_show_users_lifts_string_list(fake_client, capsys):
    fake_client.responses[("GET", "/api/users")] = ["alice", "bob"]
    cli.main(["show", "users"])
    out = capsys.readouterr().out
    assert "NAME" in out and "alice" in out and "bob" in out


def test_show_users_json_passes_raw_envelope(fake_client, capsys):
    fake_client.responses[("GET", "/api/users")] = ["alice", "bob"]
    cli.main(["--json", "show", "users"])
    out = capsys.readouterr().out
    assert json.loads(out) == ["alice", "bob"]


def test_show_features_list_then_detail(fake_client, capsys):
    fake_client.responses[("GET", "/api/features")] = ["ic", "lsq"]
    cli.main(["show", "features"])
    out = capsys.readouterr().out
    assert "ic" in out and "lsq" in out

    fake_client.responses[("GET", "/api/features/ic/tasks")] = {
        "feature": "ic",
        "tasks": [{"id": 1, "task_uuid": "T-1", "title": "carveout",
                   "status": "wip", "owners": ["alice"], "attrs": {}}],
        "aggregations": {"owners": ["alice"], "projects": ["gfc"],
                         "status_breakdown": {"wip": 1}, "eta_range": [None, None]},
    }
    capsys.readouterr()
    cli.main(["show", "features", "ic"])
    out = capsys.readouterr().out
    assert "== feature ic" in out
    assert "T-1" in out and "carveout" in out
    assert "owners:   alice" in out


def test_show_attrs_renders_key_count_samples(fake_client, capsys):
    fake_client.responses[("GET", "/api/attrs")] = [
        {"key": "eta", "count": 250, "sample_values": ["ww18", "ww19"]},
        {"key": "priority", "count": 248, "sample_values": ["P0", "P1"]},
    ]
    cli.main(["show", "attrs"])
    out = capsys.readouterr().out
    assert "eta" in out and "250" in out and "ww18,ww19" in out
    assert "priority" in out and "P0,P1" in out


def test_show_notes_list_and_detail(fake_client, capsys):
    fake_client.responses[("GET", "/api/notes")] = [
        {"id": 1, "path": "ww18/standup.md", "title": "Standup", "updated_at": "2026-04-01"},
    ]
    cli.main(["show", "notes"])
    out = capsys.readouterr().out
    assert "ww18/standup.md" in out and "Standup" in out

    fake_client.responses[("GET", "/api/notes/1")] = {
        "id": 1, "path": "ww18/standup.md", "title": "Standup",
        "etag": "abc123", "body_md": "# Standup\n\nline1\nline2\n", "updated_at": "2026-04-01",
    }
    capsys.readouterr()
    cli.main(["show", "notes", "1"])
    out = capsys.readouterr().out
    assert "id:    1" in out
    assert "path:  ww18/standup.md" in out
    assert "etag:  abc123" in out
    # Preview shows the body for short notes.
    assert "# Standup" in out and "line1" in out


def test_show_notes_path_resolves_via_listing(fake_client, capsys):
    fake_client.responses[("GET", "/api/notes")] = [
        {"id": 7, "path": "ww18/standup.md", "title": "S"},
    ]
    fake_client.responses[("GET", "/api/notes/7")] = {
        "id": 7, "path": "ww18/standup.md", "title": "S",
        "etag": "x", "body_md": "body\n", "updated_at": "x",
    }
    cli.main(["show", "notes", "ww18/standup.md"])
    out = capsys.readouterr().out
    assert "id:    7" in out
    assert "body" in out


def test_show_notes_full_dumps_entire_body(fake_client, capsys):
    big = "\n".join(f"line {i}" for i in range(50))
    fake_client.responses[("GET", "/api/notes/1")] = {
        "id": 1, "path": "x.md", "title": "X", "etag": "e",
        "body_md": big, "updated_at": "x",
    }
    cli.main(["show", "notes", "1", "--full"])
    out = capsys.readouterr().out
    assert "line 0" in out and "line 49" in out
    assert "lines total" not in out  # truncation hint suppressed by --full


def test_show_tree_indents_notes_under_projects(fake_client, capsys):
    fake_client.responses[("GET", "/api/tree")] = [
        {"project": "ww18", "role": "member", "notes": [
            {"id": 1, "path": "ww18/a.md"},
            {"id": 2, "path": "ww18/b.md"},
        ]},
    ]
    cli.main(["show", "tree"])
    out = capsys.readouterr().out
    assert "ww18  [member]  (2 notes)" in out
    assert "├─ ww18/a.md" in out
    assert "└─ ww18/b.md" in out


def test_show_agenda_passes_owner_and_days(fake_client, capsys):
    fake_client.responses[("GET", "/api/agenda")] = {
        "window": {"start": "2026-04-01", "end": "2026-04-08", "days": 7},
        "by_day": {
            "2026-04-02": [{"id": 1, "task_uuid": "T-1", "title": "x",
                            "status": "todo", "owners": ["alice"], "attrs": {}}],
        },
    }
    cli.main(["show", "agenda", "--owner", "alice", "--days", "7"])
    _, _, params, _ = fake_client.calls[0]
    assert params == {"owner": "alice", "days": 7}
    out = capsys.readouterr().out
    assert "agenda 2026-04-01 → 2026-04-08" in out
    assert "== 2026-04-02" in out and "T-1" in out


def test_show_task_renders_single_row(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks/T-1")] = {
        "id": 1, "task_uuid": "T-1", "title": "x", "status": "wip",
        "owners": ["a"], "attrs": {},
    }
    cli.main(["show", "task", "T-1"])
    out = capsys.readouterr().out
    assert "T-1" in out and "wip" in out


def test_show_task_requires_target(fake_client, capsys):
    rc = cli.main(["show", "task"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "requires a task ref" in err


def test_show_links_lists_link_rows(fake_client, capsys):
    fake_client.responses[("GET", "/api/cards/T-1/links")] = {
        "task_id": 1, "task_uuid": "T-1", "slug": "x",
        "links": [
            {"other_slug": "y", "kind": "blocks", "direction": "out"},
            {"other_slug": "z", "kind": "task", "direction": "in"},
        ],
    }
    cli.main(["show", "links", "T-1"])
    out = capsys.readouterr().out
    assert "y" in out and "blocks" in out and "out" in out
    assert "z" in out and "in" in out


def test_show_me_combines_user_and_views(fake_client, capsys):
    fake_client.responses[("GET", "/api/me")] = {"name": "alice", "is_admin": True}
    fake_client.responses[("GET", "/api/me/views")] = {"my-blocked": "status=blocked"}
    cli.main(["show", "me"])
    out = capsys.readouterr().out
    assert "name: alice (admin)" in out
    assert "saved views: 1" in out
    assert "- my-blocked" in out


def test_show_search_passes_q_param(fake_client, capsys):
    fake_client.responses[("GET", "/api/search")] = [
        {"id": 1, "path": "ww18/x.md", "title": "X"},
    ]
    cli.main(["show", "search", "carveout"])
    _, _, params, _ = fake_client.calls[0]
    assert params == {"q": "carveout"}
    out = capsys.readouterr().out
    assert "ww18/x.md" in out and "X" in out


# ---------- vn api (escape hatch) -----------------------------------------

def test_api_get_default_format_is_json(fake_client, capsys):
    fake_client.responses[("GET", "/api/admin/users")] = [
        {"name": "alice"}, {"name": "bob"},
    ]
    rc = cli.main(["api", "GET", "/api/admin/users"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == [{"name": "alice"}, {"name": "bob"}]


def test_api_path_without_leading_slash_is_normalized(fake_client, capsys):
    fake_client.responses[("GET", "/api/projects")] = []
    cli.main(["api", "GET", "api/projects"])
    method, path, _, _ = fake_client.calls[0]
    assert method == "GET" and path == "/api/projects"


def test_api_query_repeatable_and_amp_separated(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main([
        "api", "GET", "/api/tasks",
        "--query", "project=ww18",
        "--query", "kind=ar&owner=alice",
    ])
    _, _, params, _ = fake_client.calls[0]
    assert params == {"project": "ww18", "kind": "ar", "owner": "alice"}


def test_api_query_repeated_key_becomes_list(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": []}
    cli.main([
        "api", "GET", "/api/tasks",
        "--query", "attr=area:eq:fit-val",
        "--query", "attr=risk:eq:high",
    ])
    _, _, params, _ = fake_client.calls[0]
    assert params["attr"] == ["area:eq:fit-val", "risk:eq:high"]


def test_api_post_with_json_body(fake_client, capsys):
    fake_client.responses[("POST", "/api/projects")] = {"name": "ww19"}
    rc = cli.main([
        "api", "POST", "/api/projects",
        "--json-body", '{"name": "ww19"}',
    ])
    assert rc == 0
    method, path, params, body = fake_client.calls[0]
    assert method == "POST" and path == "/api/projects" and body == {"name": "ww19"}


def test_api_bad_json_body_returns_2(fake_client, capsys):
    rc = cli.main(["api", "POST", "/api/x", "--json-body", "{not json"])
    assert rc == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_api_bad_query_returns_2(fake_client, capsys):
    rc = cli.main(["api", "GET", "/api/x", "--query", "no_equals"])
    assert rc == 2
    assert "key=value" in capsys.readouterr().err


def test_api_jsonl_format_for_list_response(fake_client, capsys):
    fake_client.responses[("GET", "/api/notes")] = [
        {"id": 1, "path": "a.md"}, {"id": 2, "path": "b.md"},
    ]
    cli.main(["api", "GET", "/api/notes", "--format", "jsonl"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert json.loads(out[0])["id"] == 1
    assert json.loads(out[1])["id"] == 2


def test_api_http_error_maps_to_exit_4(monkeypatch, capsys):
    from vn.client import ApiError

    class ErrClient:
        def request(self, *a, **k):
            raise ApiError(404, '{"detail":"not found"}')
        def get(self, p, **kw): return self.request("GET", p, params=kw)

    monkeypatch.setattr(cli, "Client", lambda *a, **k: ErrClient())
    rc = cli.main(["api", "GET", "/api/missing"])
    assert rc == 4
    assert "HTTP 404" in capsys.readouterr().err


def test_api_http_5xx_maps_to_exit_5(monkeypatch, capsys):
    from vn.client import ApiError

    class ErrClient:
        def request(self, *a, **k):
            raise ApiError(500, "boom")
        def get(self, p, **kw): return self.request("GET", p, params=kw)

    monkeypatch.setattr(cli, "Client", lambda *a, **k: ErrClient())
    rc = cli.main(["api", "GET", "/api/x"])
    assert rc == 5


# ---------- --columns delta syntax (+col / -col) -------------------------

from vn.cli import _resolve_columns, _DEFAULT_COLUMNS


def test_resolve_columns_replace_mode_unchanged():
    assert _resolve_columns("id,title", _DEFAULT_COLUMNS) == ("id", "title")


def test_resolve_columns_none_returns_defaults():
    assert _resolve_columns(None, _DEFAULT_COLUMNS) == _DEFAULT_COLUMNS
    assert _resolve_columns("", _DEFAULT_COLUMNS) == _DEFAULT_COLUMNS


def test_resolve_columns_delta_add_one():
    out = _resolve_columns("+kind", _DEFAULT_COLUMNS)
    assert out == _DEFAULT_COLUMNS + ("kind",)


def test_resolve_columns_delta_remove_one():
    out = _resolve_columns("-status", _DEFAULT_COLUMNS)
    assert out == ("id", "priority", "eta", "owners", "title")


def test_resolve_columns_delta_add_and_remove_combined():
    out = _resolve_columns("+kind,-status", _DEFAULT_COLUMNS)
    assert out == ("id", "priority", "eta", "owners", "title", "kind")


def test_resolve_columns_delta_add_idempotent():
    # Adding a column that's already in defaults is a no-op (no duplicates).
    out = _resolve_columns("+id,+kind", _DEFAULT_COLUMNS)
    assert out.count("id") == 1 and out[-1] == "kind"


def test_resolve_columns_delta_remove_unknown_is_noop():
    out = _resolve_columns("-doesnotexist", _DEFAULT_COLUMNS)
    assert out == _DEFAULT_COLUMNS


def test_resolve_columns_mixed_modes_rejected():
    import pytest as _pt
    with _pt.raises(ValueError, match="cannot mix"):
        _resolve_columns("id,+kind", _DEFAULT_COLUMNS)


def test_resolve_columns_lowercases_names():
    assert _resolve_columns("ID,Title", _DEFAULT_COLUMNS) == ("id", "title")
    out = _resolve_columns("+KIND,-STATUS", _DEFAULT_COLUMNS)
    assert "kind" in out and "status" not in out


def test_list_delta_columns_end_to_end(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks")] = {"tasks": [
        {"id": "T-1", "status": "wip", "priority": "P1", "eta": None,
         "owners": ["alice"], "title": "demo", "kind": "task"},
    ]}
    cli.main(["list", "--columns", "+kind,-status"])
    out = capsys.readouterr().out
    headers = out.splitlines()[0].split()
    assert headers == ["ID", "PRIORITY", "ETA", "OWNERS", "TITLE", "KIND"]


def test_show_task_delta_columns_end_to_end(fake_client, capsys):
    fake_client.responses[("GET", "/api/tasks/T-1")] = {
        "id": "T-1", "status": "wip", "priority": "P2", "eta": None,
        "owners": ["bob"], "title": "thing", "kind": "ar",
    }
    cli.main(["show", "task", "T-1", "--columns", "+kind"])
    headers = capsys.readouterr().out.splitlines()[0].split()
    assert headers[-1] == "KIND" and "STATUS" in headers


def test_list_mixed_columns_returns_2(fake_client, capsys):
    rc = cli.main(["list", "--columns", "id,+kind"])
    assert rc == 2
    assert "cannot mix" in capsys.readouterr().err


# ---------- vn me <subcommand> ---------------------------------------------

_FAKE_STATS = {
    "as_of": "2026-04-27",
    "tasks_closed": {"today": 3, "week": 11, "month": 28, "lifetime": 142},
    "notes_touched": {"week": 6, "month": 19},
    "current_streak_days": 7,
    "longest_streak_days": 23,
    "rest_tokens_remaining": 1,
    "on_time_eta_rate_30d": 0.81,
    "on_time_sample_30d": 26,
    "favorite_project_30d": "ww18",
    "by_kind": {"task": 26, "ar": 2},
}


def test_me_stats_renders_card(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/stats")] = _FAKE_STATS
    rc = cli.main(["me", "stats"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "as of 2026-04-27" in out
    assert "closed today      3" in out
    assert "current streak    7 day(s)" in out
    assert "favorite project  ww18" in out
    assert "on-time ETA (30d) 81%" in out


def test_me_stats_json_passthrough(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/stats")] = _FAKE_STATS
    rc = cli.main(["--json", "me", "stats"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current_streak_days"] == 7


def test_me_streak_compact(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/streak")] = {
        "current_streak_days": 5, "longest_streak_days": 11,
        "rest_tokens_remaining": 2, "as_of": "2026-04-27",
    }
    rc = cli.main(["me", "streak"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "5 day(s)" in out
    assert "longest: 11" in out
    assert "rest tokens: 2" in out


def test_me_streak_zero_no_flame(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/streak")] = {
        "current_streak_days": 0, "longest_streak_days": 4,
        "rest_tokens_remaining": 2, "as_of": "2026-04-27",
    }
    cli.main(["me", "streak"])
    out = capsys.readouterr().out
    # No flame when current == 0
    assert "🔥" not in out


def test_me_history_sparkline(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/history")] = [
        {"date": "2026-04-21", "closes": 0, "edits": 1},
        {"date": "2026-04-22", "closes": 2, "edits": 3},
        {"date": "2026-04-23", "closes": 1, "edits": 0},
    ]
    rc = cli.main(["me", "history", "--days", "3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "closes" in out and "edits" in out
    assert "total 3" in out  # 0+2+1
    assert "total 4" in out  # 1+3+0
    # The history endpoint was called with the requested days param.
    call = [c for c in fake_client.calls if c[1] == "/api/me/history"][0]
    assert call[2] == {"days": 3}


def test_me_history_empty(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/history")] = []
    cli.main(["me", "history"])
    assert "no activity" in capsys.readouterr().out


def test_me_activity_filters(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/activity")] = [
        {"id": 1, "kind": "task.closed", "ref": "T-X", "ts": "2026-04-27T10:00:00",
         "meta": {"from": "todo", "to": "done"}},
    ]
    rc = cli.main(["me", "activity", "--kind", "task.closed", "--limit", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "task.closed" in out and "T-X" in out
    call = [c for c in fake_client.calls if c[1] == "/api/me/activity"][0]
    assert call[2] == {"kind": "task.closed", "limit": 5}


def test_me_activity_empty(fake_client, capsys):
    fake_client.responses[("GET", "/api/me/activity")] = []
    cli.main(["me", "activity"])
    assert "no events" in capsys.readouterr().out


def test_me_subcommand_required(fake_client, capsys):
    with pytest.raises(SystemExit):
        cli.main(["me"])
