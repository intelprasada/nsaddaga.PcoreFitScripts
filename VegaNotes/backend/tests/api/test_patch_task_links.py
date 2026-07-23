"""Tests for #314 PATCH /tasks/{ref} with url/hsd/jira/pr link tokens.

Confirms:
- PATCH with `hsd=[...]` rewrites the markdown line + updates TaskAttr.
- Empty list clears all tokens for that key.
- Whitespace-in-value gets rejected with 400.
- Round-trip: PATCH -> GET returns the new values via task.attrs.
- Multiple link kinds can be patched in one call.
- Ref-row propagation: adding hsd on the canonical decl also updates
  #task ref rows in other files (mirrors features behavior).
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select


DATA = Path(tempfile.mkdtemp(prefix="vega-314-link-patch-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
import app.db as _db_mod  # noqa: E402


AUTH_ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    saved_data_dir = settings.data_dir
    saved_engine = _db_mod._engine
    saved_archive_engine = _db_mod._archive_engine

    settings.data_dir = DATA
    _db_mod._engine = None
    _db_mod._archive_engine = None
    _db_mod.init_db()

    with TestClient(app) as c:
        yield c

    settings.data_dir = saved_data_dir
    _db_mod._engine = saved_engine
    _db_mod._archive_engine = saved_archive_engine
    shutil.rmtree(DATA, ignore_errors=True)


def _put_note(c: TestClient, path: str, body: str):
    r = c.put(
        "/api/notes",
        json={"path": path, "body_md": body},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text


def _get_task(c: TestClient, task_ref) -> dict:
    r = c.get(f"/api/tasks/{task_ref}", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return r.json()


def _patch_task(c: TestClient, task_ref, patch: dict) -> tuple[int, dict]:
    r = c.patch(
        f"/api/tasks/{task_ref}",
        json=patch,
        headers={"Authorization": AUTH_ADMIN},
    )
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


def _read_disk(rel_path: str) -> str:
    return (settings.notes_dir / rel_path).read_text(encoding="utf-8")


def _as_list(v) -> list[str]:
    """The task-dict serializer flattens single-row attrs to a scalar
    string.  Normalize both shapes into a list for equality asserts.
    """
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def test_patch_adds_hsd_token_to_markdown_and_index(client):
    path = "p314-add/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0001 Ship @admin\n")

    status, _ = _patch_task(client, "T-LNK0001", {"hsd": ["1234567"]})
    assert status == 200

    md = _read_disk(path)
    assert "#hsd 1234567" in md

    j = _get_task(client, "T-LNK0001")
    assert _as_list(j["attrs"].get("hsd")) == ["1234567"]


def test_patch_replaces_existing_hsd_values(client):
    path = "p314-replace/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0002 Ship @admin #hsd 111 #hsd 222\n")
    assert _as_list(_get_task(client, "T-LNK0002")["attrs"].get("hsd")) == ["111", "222"]

    status, _ = _patch_task(client, "T-LNK0002", {"hsd": ["333"]})
    assert status == 200

    j = _get_task(client, "T-LNK0002")
    assert _as_list(j["attrs"].get("hsd")) == ["333"]
    md = _read_disk(path)
    assert "#hsd 111" not in md
    assert "#hsd 222" not in md
    assert "#hsd 333" in md


def test_patch_empty_list_clears_hsd(client):
    path = "p314-clear/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0003 Ship @admin #hsd 1234567\n")

    status, _ = _patch_task(client, "T-LNK0003", {"hsd": []})
    assert status == 200

    j = _get_task(client, "T-LNK0003")
    assert "hsd" not in j["attrs"] or j["attrs"]["hsd"] in ([], None)
    assert "#hsd" not in _read_disk(path)


def test_patch_multiple_link_kinds_in_one_call(client):
    path = "p314-multi/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0004 Ship @admin\n")

    status, _ = _patch_task(client, "T-LNK0004", {
        "hsd":  ["7654321"],
        "jira": ["ABC-42"],
        "pr":   ["intelprasada/veganotes#309"],
        "url":  ["https://example.com/spec"],
    })
    assert status == 200

    j = _get_task(client, "T-LNK0004")
    assert _as_list(j["attrs"].get("hsd"))  == ["7654321"]
    assert _as_list(j["attrs"].get("jira")) == ["ABC-42"]
    assert _as_list(j["attrs"].get("pr"))   == ["intelprasada/veganotes#309"]
    assert _as_list(j["attrs"].get("url"))  == ["https://example.com/spec"]

    md = _read_disk(path)
    for tok in [
        "#hsd 7654321",
        "#jira ABC-42",
        "#pr intelprasada/veganotes#309",
        "#url https://example.com/spec",
    ]:
        assert tok in md, f"missing {tok!r} in {md!r}"


def test_patch_rejects_whitespace_in_link_value(client):
    path = "p314-invalid/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0005 Ship @admin\n")

    status, body = _patch_task(client, "T-LNK0005", {"url": ["https://example.com/foo bar"]})
    assert status == 400
    assert "whitespace" in str(body).lower()


def test_patch_url_supports_label_prefix(client):
    path = "p314-label/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0006 Ship @admin\n")

    status, _ = _patch_task(client, "T-LNK0006", {"url": ["Design:https://example.com/design"]})
    assert status == 200

    j = _get_task(client, "T-LNK0006")
    assert _as_list(j["attrs"].get("url")) == ["Design:https://example.com/design"]
    assert "#url Design:https://example.com/design" in _read_disk(path)


def test_patch_link_tokens_propagate_to_ref_rows(client):
    """PATCH on the canonical decl must also rewrite #task ref rows in
    other .md files, matching the propagation pattern used by features
    (#92 follow-up).  This keeps the cross-file mirror consistent so a
    future reindex can't push a stale ref row's value back over the
    canonical patch.
    """
    canonical = "p314-refs/canonical.md"
    reffer = "p314-refs/other.md"
    _put_note(client, canonical, "# c\n!task #id T-LNK0007 Ship @admin\n")
    _put_note(client, reffer, "# o\n- #task T-LNK0007\n")

    status, _ = _patch_task(client, "T-LNK0007", {"hsd": ["9999"]})
    assert status == 200

    canonical_md = _read_disk(canonical)
    other_md = _read_disk(reffer)
    assert "#hsd 9999" in canonical_md
    assert "#hsd 9999" in other_md, (
        "ref-row propagation must apply the same #hsd token to referring "
        "files so the cross-file mirror stays consistent"
    )


# ── #316: MD-link URL values ──────────────────────────────────────────────

def test_patch_url_accepts_md_link_form_with_internal_spaces(client):
    """#316: whitespace INSIDE a `[Label](url)` MD-link value is legal;
    the whole bracketed span persists verbatim and re-reads correctly.
    """
    path = "p316-md/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0016 Ship @admin\n")

    md_val = "[Design Doc](https://example.com/design)"
    status, _ = _patch_task(client, "T-LNK0016", {"url": [md_val]})
    assert status == 200, "MD-link URL values must be accepted despite internal spaces"

    disk = _read_disk(path)
    assert f"#url {md_val}" in disk

    j = _get_task(client, "T-LNK0016")
    assert _as_list(j["attrs"].get("url")) == [md_val]


def test_patch_url_md_link_replace_cleans_prior_md_link_fully(client):
    """Replacing an MD-form URL must strip the entire prior `[...]()`
    span, not just the `[Label` prefix — otherwise the old label leaks
    into the file as leftover prose.
    """
    path = "p316-replace/w1.md"
    old = "[Design Doc](https://example.com/old)"
    _put_note(
        client, path,
        f"# t\n!task #id T-LNK0017 Ship @admin #url {old}\n",
    )
    assert _as_list(_get_task(client, "T-LNK0017")["attrs"].get("url")) == [old]

    new = "[Fresh Doc](https://example.com/new)"
    status, _ = _patch_task(client, "T-LNK0017", {"url": [new]})
    assert status == 200

    disk = _read_disk(path)
    assert "Design Doc" not in disk, (
        "the previous MD-link label must be fully removed, not left as "
        "orphaned prose after the replace"
    )
    assert f"#url {new}" in disk


def test_patch_url_still_rejects_bare_whitespace_value(client):
    """Whitespace in a bare (non-MD) URL is still rejected; MD form is
    the only whitespace-tolerant shape."""
    path = "p316-bare-ws/w1.md"
    _put_note(client, path, "# t\n!task #id T-LNK0018 Ship @admin\n")

    status, body = _patch_task(
        client, "T-LNK0018", {"url": ["https://example.com/foo bar"]},
    )
    assert status == 400
    assert "whitespace" in str(body).lower()
