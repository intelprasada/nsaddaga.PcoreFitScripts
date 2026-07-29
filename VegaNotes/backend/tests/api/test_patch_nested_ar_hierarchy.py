"""Integration guard: editing any attr on a *nested* AR must not reparent it.

The older attr-edit API tests all used a flat, column-0 `!task` — a shape where
the indent-collapse bug (T-800631) is structurally invisible. This seeds the
shape real notes use (a task indented under a heading, ARs one level deeper)
and asserts, for every mutable field, that after PATCHing one AR:
  * the parent task still lists all its ARs, and
  * the edited AR keeps its indentation on disk.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-nested-ar-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
import app.db as _db_mod  # noqa: E402


AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()

# A task indented one level under the H1, with three ARs one level deeper.
NOTE_BODY = (
    "# Weekly\n"
    "\t!task #id T-NEST01 Parent debug #status in-progress\n"
    "\t\t!AR #id T-NAR001 first ar @njammala #status todo\n"
    "\t\t!AR #id T-NAR002 second ar @njammala #status todo\n"
    "\t\t!AR #id T-NAR003 third ar @njammala #status done\n"
)
NOTE_PATH = "nest/w1.md"


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


def _seed(c):
    r = c.put("/api/notes", json={"path": NOTE_PATH, "body_md": NOTE_BODY},
              headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text


def _ar_count(c) -> int:
    r = c.get("/api/tasks/T-NEST01?include_children=true", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    return len([x for x in (r.json().get("children") or []) if x.get("kind") == "ar"])


def _ar_line_lead(ar_id: str = "T-NAR001") -> str:
    md = (settings.notes_dir / NOTE_PATH).read_text(encoding="utf-8")
    line = next(ln for ln in md.splitlines() if ar_id in ln and "!AR" in ln)
    return line[: len(line) - len(line.lstrip())]


def test_baseline_hierarchy(client):
    _seed(client)
    assert _ar_count(client) == 3
    assert _ar_line_lead("T-NAR001") == "\t\t"


# Every mutable field the popover can send, patched onto the first (nested) AR.
FIELD_PATCHES = [
    ("status",   {"status": "done"}),
    ("priority", {"priority": "P1"}),
    ("eta",      {"eta": "ww30"}),
    ("owners",   {"owners": ["bob"]}),
    ("features", {"features": ["fv"]}),
    ("hsd",      {"hsd": ["14028322043"]}),   # the exact T-800631 trigger
    ("jira",     {"jira": ["ABC-42"]}),
    ("pr",       {"pr": ["owner/repo#1"]}),
    ("url",      {"url": ["[Doc](https://example.com/x)"]}),
    ("progress", {"progress": "3/5"}),
    ("add_tag",  {"add_tag": "urgent"}),
]


@pytest.mark.parametrize("field,patch", FIELD_PATCHES, ids=[f[0] for f in FIELD_PATCHES])
def test_edit_nested_ar_keeps_all_ars(client, field, patch):
    # Fresh note each time so the fields are tested in isolation.
    _seed(client)
    assert _ar_count(client) == 3, "precondition"
    r = client.patch("/api/tasks/T-NAR001", json=patch, headers={"Authorization": AUTH})
    assert r.status_code == 200, f"{field}: {r.status_code} {r.text}"
    # The parent must STILL show all three ARs (the bug dropped this to 0/1).
    assert _ar_count(client) == 3, f"{field}: ARs reparented -> {_ar_count(client)}"
    # And the edited AR keeps its two-tab indent on disk.
    assert _ar_line_lead("T-NAR001") == "\t\t", f"{field}: indent changed"
