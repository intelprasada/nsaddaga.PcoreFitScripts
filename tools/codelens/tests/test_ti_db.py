"""Unit tests for ti_db.TiDb (pure-logic; mocks turnininfo + git)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parents[1] / "ti_db.py"
_spec = importlib.util.spec_from_file_location("ti_db", _MOD)
T = importlib.util.module_from_spec(_spec)
sys.modules["ti_db"] = T
_spec.loader.exec_module(T)


def _mk_records():
    """Two on-master TIs + one cancelled (should be filtered out).

    TI 100 (older, bundle b100): touches file A, brings shas s1, s2.
    TI 200 (newer, bundle b200): touches files A, B, brings shas s3, s4.
    TI 999 (cancelled): touches A. Should NOT be in by_id.
    """
    return [
        {"id": 100, "bundle_commit": "b100", "cluster": "core",
         "stepping": "gfc-a0", "branch": "master", "status": "released",
         "completed_time_epoch": 1000, "files_changed": ["A"]},
        {"id": 200, "bundle_commit": "b200", "cluster": "core",
         "stepping": "gfc-a0", "branch": "master", "status": "released",
         "completed_time_epoch": 2000, "files_changed": ["A", "B"]},
        {"id": 999, "bundle_commit": "b999", "cluster": "core",
         "stepping": "gfc-a0", "branch": "master", "status": "cancelled",
         "completed_time_epoch": 1500, "files_changed": ["A"]},
    ]


def _mk_db(tmp_path, records=None, sha_sets=None):
    monkey_store = tmp_path / "ti_db"
    monkey_store.mkdir()
    T._STORE = monkey_store  # test override; module-level
    sha_sets = sha_sets or {
        "b100": frozenset(["s1", "s2"]),
        "b200": frozenset(["s3", "s4"]),
    }
    def _fake_merge_set(root, msha):
        return sha_sets.get(msha, frozenset())
    db = T.TiDb("core", "gfc-a0", "master", repo_root="/tmp/repo",
                merge_set_fn=_fake_merge_set)
    db._install(records if records is not None else _mk_records())
    return db


def test_install_filters_and_indexes(tmp_path):
    db = _mk_db(tmp_path)
    # 999 is cancelled → should not appear even though we passed it in via
    # _install directly? _install doesn't filter — refresh() does. So all 3
    # are indexed here. Test the filter behavior in test_refresh_filters.
    assert set(db.by_id.keys()) == {"100", "200", "999"}
    assert set(db.by_file["A"]) == {"100", "200", "999"}
    assert db.by_file["B"] == ["200"]
    assert db.by_bundle_commit["b100"] == "100"


def test_tis_for_file_sorted_newest_first(tmp_path):
    db = _mk_db(tmp_path)
    recs = db.tis_for_file("A")
    # newest completed_time_epoch first
    ids = [str(r["id"]) for r in recs]
    assert ids[0] == "200"


def test_attribute_shas_oldest_wins(tmp_path):
    # sha s2 belongs to BOTH b100 and b200 (edge case, shouldn't happen
    # in prod but we handle it): older wins.
    db = _mk_db(tmp_path, sha_sets={
        "b100": frozenset(["s1", "s2"]),
        "b200": frozenset(["s2", "s3", "s4"]),
        "b999": frozenset(["s5"]),
    })
    got = db.attribute_shas(["s1", "s2", "s3", "s4", "orphan"], "A")
    assert got["s1"] == "100"
    assert got["s2"] == "100"       # older wins
    assert got["s3"] == "200"
    assert got["s4"] == "200"
    assert got["orphan"] is None


def test_attribute_shas_orphan_when_no_candidates(tmp_path):
    db = _mk_db(tmp_path)
    got = db.attribute_shas(["s1"], "unknown_file")
    assert got["s1"] is None


def test_attribute_shas_ignores_ti_not_in_file_candidates(tmp_path):
    # sha s5 is only in b999 (which touches file A), but querying for file B
    # should NOT credit s5 to any TI even though b999 might contain it.
    # Actually in our expander, file B only has TI 200 as candidate — so
    # s5 (which is in b999's set, not b200's) must be orphan.
    db = _mk_db(tmp_path, sha_sets={
        "b100": frozenset(["s1"]),
        "b200": frozenset(["s3"]),
        "b999": frozenset(["s5"]),
    })
    got = db.attribute_shas(["s5"], "B")
    assert got["s5"] is None


def test_ti_lookup(tmp_path):
    db = _mk_db(tmp_path)
    assert db.ti(200)["bundle_commit"] == "b200"
    assert db.ti("100")["bundle_commit"] == "b100"
    assert db.ti(12345) is None


def test_disk_roundtrip(tmp_path):
    monkey_store = tmp_path / "ti_db"
    monkey_store.mkdir()
    T._STORE = monkey_store
    records = [
        {"id": 100, "bundle_commit": "b100", "cluster": "core",
         "stepping": "gfc-a0", "branch": "master", "status": "released",
         "completed_time_epoch": 1000, "files_changed": ["A"]},
    ]
    db1 = T.TiDb("core", "gfc-a0", "master", repo_root="/tmp/repo")
    db1.as_of_epoch = 12345
    db1._install(records)
    db1._save_disk(records)

    db2 = T.TiDb("core", "gfc-a0", "master", repo_root="/tmp/repo")
    assert db2._load_disk() is True
    assert db2.as_of_epoch == 12345
    assert "100" in db2.by_id


def test_refresh_filters_cancelled_and_no_bundle(tmp_path, monkeypatch):
    """refresh() must drop status != released/accepted AND records with no
    bundle_commit."""
    monkey_store = tmp_path / "ti_db"
    monkey_store.mkdir()
    T._STORE = monkey_store

    # Fake `turnininfo` by monkey-patching subprocess.run to write our
    # expected output file.
    raw_records = [
        {"id": 100, "bundle_commit": "b100", "status": "released",
         "cluster": "core", "stepping": "gfc-a0", "branch": "master",
         "completed_time_epoch": 1000, "files_changed": ["A"]},
        {"id": 200, "bundle_commit": "b200", "status": "cancelled",
         "cluster": "core", "stepping": "gfc-a0", "branch": "master",
         "completed_time_epoch": 2000, "files_changed": ["A"]},
        {"id": 300, "bundle_commit": None, "status": "released",
         "cluster": "core", "stepping": "gfc-a0", "branch": "master",
         "completed_time_epoch": 3000, "files_changed": ["A"]},
        {"id": 400, "bundle_commit": "b400", "status": "accepted",
         "cluster": "core", "stepping": "gfc-a0", "branch": "master",
         "completed_time_epoch": 4000, "files_changed": ["B"]},
    ]

    class _Fake:
        returncode = 0
        stderr = ""
        stdout = ""

    def _run(args, **kw):
        # args[-1] is the -output path
        out_path = Path(args[-1])
        out_path.write_text(json.dumps(raw_records))
        return _Fake()

    monkeypatch.setattr(T.subprocess, "run", _run)
    db = T.TiDb("core", "gfc-a0", "master", repo_root="/tmp/repo")
    db.refresh()
    assert db.error == ""
    # 200 (cancelled) and 300 (no bundle_commit) filtered out.
    assert set(db.by_id.keys()) == {"100", "400"}
    assert db.as_of_epoch > 0
