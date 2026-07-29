"""Tests for #320 `/tasks` filter params: progress_min_pct / max / has.

Confirms:
- ``progress_has=1`` narrows to only tasks carrying a `#progress` token.
- ``progress_min_pct=50`` keeps only tasks with a *ratio* whose
  percent is at least the threshold.
- ``progress_max_pct=25`` inclusive upper bound.
- Bare counters (`#progress 42`, no denominator) are kept by
  ``progress_has`` but excluded from min/max_pct filters — a bare
  counter has no percent.
- Combining ``min_pct`` and ``max_pct`` narrows to an inclusive band.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-320-tasks-filter-"))
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
        _seed(c)
        yield c

    settings.data_dir = saved_data_dir
    _db_mod._engine = saved_engine
    _db_mod._archive_engine = saved_archive_engine
    shutil.rmtree(DATA, ignore_errors=True)


def _put(c: TestClient, path: str, body: str):
    r = c.put(
        "/api/notes",
        json={"path": path, "body_md": body},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text


def _seed(c: TestClient) -> None:
    # 5 tasks with different progress shapes + one bare with no progress.
    _put(c, "flt320/w1.md",
         "# t\n"
         "!task #id T-FLT0001 Low @admin #progress 5/100\n"          # 5%
         "!task #id T-FLT0002 Mid @admin #progress 40/100 wip\n"     # 40%
         "!task #id T-FLT0003 High @admin #progress 90/100 fixed\n"  # 90%
         "!task #id T-FLT0004 Counter @admin #progress 42\n"         # bare
         "!task #id T-FLT0005 NoProgress @admin\n"                   # none
         "!task #id T-FLT0006 Full @admin #progress 100/100\n"       # 100%
    )


def _titles(c: TestClient, params: dict) -> list[str]:
    r = c.get("/api/tasks", params=params, headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return sorted(t["title"] for t in r.json()["tasks"])


def test_progress_has_keeps_only_tasks_with_the_token(client):
    got = _titles(client, {"progress_has": 1, "top_level_only": 1})
    # 5 of the 6 tasks have `#progress`; NoProgress is filtered out.
    assert "NoProgress" not in got
    assert set(got) >= {"Low", "Mid", "High", "Counter", "Full"}


def test_progress_min_pct_50_keeps_high_and_full(client):
    got = _titles(client, {"progress_min_pct": 50, "top_level_only": 1})
    assert "High" in got
    assert "Full" in got
    assert "Low" not in got
    assert "Mid" not in got
    # Bare counter (Counter) is skipped by min/max_pct.
    assert "Counter" not in got
    assert "NoProgress" not in got


def test_progress_max_pct_10_keeps_only_low(client):
    got = _titles(client, {"progress_max_pct": 10, "top_level_only": 1})
    assert got == ["Low"]


def test_progress_pct_band_25_75_keeps_only_mid(client):
    got = _titles(client,
                  {"progress_min_pct": 25, "progress_max_pct": 75,
                   "top_level_only": 1})
    assert got == ["Mid"]


def test_progress_max_pct_boundary_is_inclusive(client):
    got = _titles(client, {"progress_max_pct": 100, "top_level_only": 1})
    # All *ratio* tasks are ≤ 100 %; bare counter skipped.
    assert "Full" in got
    assert "High" in got
    assert "Mid" in got
    assert "Low" in got
    assert "Counter" not in got


def test_progress_min_pct_100_only_matches_full(client):
    got = _titles(client, {"progress_min_pct": 100, "top_level_only": 1})
    assert got == ["Full"]


def test_progress_params_reject_out_of_range(client):
    r = client.get(
        "/api/tasks",
        params={"progress_min_pct": -1},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 422
