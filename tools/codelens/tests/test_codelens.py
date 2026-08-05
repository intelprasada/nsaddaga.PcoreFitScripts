"""Unit tests for codelens_server pure-logic helpers.

These avoid any live git repo so they run anywhere (Makefile / CI). The
git-backed paths are exercised by the end-to-end checks in the README.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the sibling module by path (tools/ isn't an installed package).
_MOD = Path(__file__).resolve().parents[1] / "codelens_server.py"
_spec = importlib.util.spec_from_file_location("codelens_server", _MOD)
C = importlib.util.module_from_spec(_spec)
sys.modules["codelens_server"] = C
_spec.loader.exec_module(C)


# --- commit-message parsing -------------------------------------------------
def test_extract_hsds_basic():
    msg = "Need to add fix as RTL not providing SB_hit HSD:22022010679"
    assert C._extract_hsds(msg) == ["22022010679"]


def test_extract_hsds_dedup_and_multiple():
    msg = "bug fixes: 14025998994 13013909829 14025998994"
    assert C._extract_hsds(msg) == ["14025998994", "13013909829"]


def test_extract_hsds_ignores_short_numbers():
    # workweek-ish / small numbers must not be mistaken for HSD ids
    assert C._extract_hsds("fix 24ww32 line 42 bug 7") == []


def test_turnin_regex_variants():
    ids = {m.group(1) for m in C._TURNIN_RE.finditer(
        "Merge user_turnin4787 and integrate_bundle22027 (turnin 16331)")}
    assert ids == {"4787", "22027", "16331"}


def test_turnin_regex_two_digit_bundle():
    # early integrate_bundle / user_turnin ids are only 2 digits and must
    # still be picked up (regression: they were dropped by a \d{3,7} bound).
    ids = {m.group(1) for m in C._TURNIN_RE.finditer(
        "Merge branch 'master' of .../core/integrate_bundle78 user_turnin42")}
    assert ids == {"78", "42"}


# --- name / idsid helpers ---------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Mostovicz, Tsvi", "Tsvi Mostovicz"),
    ("Li, Yongxi", "Yongxi Li"),
    ("Tsvi Mostovicz", "Tsvi Mostovicz"),
    ("  Singh,   Abhijit ", "Abhijit Singh"),
])
def test_normalize_name(raw, expected):
    assert C._normalize_name(raw) == expected


def test_idsid_from_email_single_token_only():
    assert C._idsid_from_email("<gbandana@intel.com>") == "gbandana"
    # first.last form is NOT an idsid
    assert C._idsid_from_email("tsvi.mostovicz@intel.com") == ""
    assert C._idsid_from_email("") == ""


# --- language mapping -------------------------------------------------------
def test_lang_by_ext():
    assert C._LANG_BY_EXT[".sv"] == "systemverilog"
    assert C._LANG_BY_EXT[".vs"] == "systemverilog"
    assert C._LANG_BY_EXT[".py"] == "python"


# --- range clamping ---------------------------------------------------------
def test_clamp_range_orders_and_caps():
    assert C._clamp_range(10, 5) == (10, 10)          # end < start -> single line
    assert C._clamp_range(0, 3) == (1, 3)             # start floored to 1
    s, e = C._clamp_range(1, 10_000_000)
    assert e - s + 1 == C._MAX_RANGE_LINES            # capped


# --- blame porcelain parsing ------------------------------------------------
def test_parse_porcelain():
    sha = "9d297d06205578c223fdbf981624cf823844c2fd"
    porcelain = (
        f"{sha} 1 1 2\n"
        "author Mostovicz, Tsvi\n"
        "author-mail <tsvi.mostovicz@intel.com>\n"
        "author-time 1723127436\n"
        "author-tz +0300\n"
        "summary Initial commit PNC clean - 24ww32d\n"
        "filename core/fe/rtl/baaddpvd.vs\n"
        "\t// line one\n"
        f"{sha} 2 2\n"
        "\t// line two\n"
    )
    lines = C._parse_porcelain(porcelain, start=1)
    assert [l["line"] for l in lines] == [1, 2]
    assert lines[0]["short"] == sha[:12]
    assert lines[0]["author"] == "Mostovicz, Tsvi"
    assert lines[0]["mail"] == "tsvi.mostovicz@intel.com"
    assert lines[0]["time"] == 1723127436
    assert lines[0]["summary"] == "Initial commit PNC clean - 24ww32d"


# --- path-traversal guard ---------------------------------------------------
def test_safe_fs_path_blocks_traversal(tmp_path):
    base = tmp_path / "core" / "fe"
    (base / "rtl").mkdir(parents=True)
    (base / "rtl" / "a.vs").write_text("x")
    repo = C.RepoInfo.__new__(C.RepoInfo)   # bypass git resolution
    repo.root = str(tmp_path)
    repo.base = "core/fe"
    assert repo.safe_fs_path("rtl/a.vs") is not None
    assert repo.safe_fs_path("../../../etc/passwd") is None
    assert repo.safe_fs_path("rtl/../../secret") is None


# --- TI / commit lens helpers -----------------------------------------------
def test_ti_token_re_matches_whole_number():
    assert C._ti_token_re("4787").search("…/incoming/fit/user_turnin4787")
    # must not match the id embedded in a longer number
    assert not C._ti_token_re("4787").search("user_turnin47870")
    assert not C._ti_token_re("4787").search("14787")


def test_incoming_ti_re_extracts_bundle():
    subj = "Merge branch 'master' of /nfs/site/proj/jnc/jnc.basedir.02/incoming/fit/user_turnin4787"
    m = C._INCOMING_TI_RE.search(subj)
    assert m and m.group(1) == "user_turnin4787"
    assert C._INCOMING_TI_RE.search("Merge /p/hdk/.../fit-jnc-a0-master-latest") is None


def test_short_sha():
    assert C._short("f39984ebb46dfd59a4cd7fd0a3870b120c4cb4a5") == "f39984ebb46d"
    assert C._short("abc") == "abc"


def test_files_stat_totals():
    files = [
        {"add": 10, "del": 2}, {"add": 0, "del": 5}, {"add": 3, "del": 3},
    ]
    assert C._files_stat(files) == {"files": 3, "add": 13, "del": 10}
    assert C._files_stat([]) == {"files": 0, "add": 0, "del": 0}


class _StubRepo:
    """Minimal RepoInfo stand-in that returns canned `git diff` output so the
    pure parsing in _diff_files can be tested without a real repo."""
    def __init__(self, numstat: str, namestatus: str):
        self._numstat, self._namestatus = numstat, namestatus

    def _git(self, *args, timeout=90):
        import types
        out = self._numstat if "--numstat" in args else self._namestatus
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")


def test_diff_files_parses_and_sorts_by_churn():
    numstat = ("10\t2\tcore/fe/a.vs\n"
               "0\t5\tcore/fe/b.vs\n"
               "-\t-\tcore/fe/img.png\n"
               "3\t3\tcore/fe/c.vs\n")
    namestatus = ("M\tcore/fe/a.vs\n"
                  "D\tcore/fe/b.vs\n"
                  "A\tcore/fe/img.png\n"
                  "M\tcore/fe/c.vs\n")
    files = C._diff_files(_StubRepo(numstat, namestatus), "BASE", "TARGET")
    # sorted by (add+del) descending, tie broken by path
    assert [f["path"] for f in files] == [
        "core/fe/a.vs", "core/fe/c.vs", "core/fe/b.vs", "core/fe/img.png"]
    a = files[0]
    assert (a["add"], a["del"], a["status"], a["binary"]) == (10, 2, "M", False)
    b = next(f for f in files if f["path"] == "core/fe/b.vs")
    assert (b["add"], b["del"], b["status"]) == (0, 5, "D")
    img = next(f for f in files if f["path"].endswith(".png"))
    assert img["binary"] is True and img["add"] == 0 and img["status"] == "A"


# --- fidelity: file-scope TI list is DERIVED from commit list ---------------
def test_build_file_tis_derives_from_commits(monkeypatch):
    """C1/D3 round-trip: the TI list must be exactly the union of per-commit
    turnins, so a TI can never appear that no commit maps to (and vice versa).
    We stub build_file_commits with hand-crafted commits and check the
    aggregation."""
    fake_commits = {
        "n_commits": 3, "commits": [
            {"sha": "a"*40, "short": "a"*12, "author": "X", "time": 100,
             "summary": "fix CTE hazard", "turnins": ["4787"], "hsds": [],
             "merge": "m1"*6, "merge_summary": "Merge user_turnin4787",
             "add": 5, "del": 2, "binary": False},
            {"sha": "b"*40, "short": "b"*12, "author": "Y", "time": 200,
             "summary": "add coverage bin", "turnins": ["4787", "78"], "hsds": [],
             "merge": "m1"*6, "merge_summary": "Merge user_turnin4787",
             "add": 1, "del": 0, "binary": False},
            {"sha": "c"*40, "short": "c"*12, "author": "X", "time": 50,
             "summary": "noop", "turnins": [],  # <- no TI → must not contribute
             "hsds": [], "merge": "",  "merge_summary": "",
             "add": 3, "del": 3, "binary": False},
        ],
        "path": "f", "repo": "R", "head": "H", "follow": True,
        "truncated": False,
    }

    class _R:  # stub RepoInfo
        key = "R"; head = "H"
        def _git(self, *a, **k):
            import types
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(C, "build_file_commits",
                        lambda repo, rel, follow=True, force=False: fake_commits)
    d = C.build_file_tis(_R(), "f")

    tids = {t["id"] for t in d["tis"]}
    assert tids == {"4787", "78"}                    # C2: no phantom TIs
    per_commit = set()
    for c in fake_commits["commits"]:
        per_commit.update(c["turnins"])
    assert tids == per_commit                        # D3: exact round-trip

    by_id = {t["id"]: t for t in d["tis"]}
    assert by_id["4787"]["n_commits"] == 2           # aggregation correct
    assert by_id["78"]["n_commits"] == 1
    assert by_id["4787"]["add"] == 6                 # +5 +1
    assert by_id["4787"]["del"] == 2
    assert d["n_commits"] == 3
    # Consolidated summary is built from non-merge commit subjects, not the
    # merge boilerplate — and dedupes across commits.
    assert set(by_id["4787"]["subjects"]) == {"fix CTE hazard", "add coverage bin"}
    assert "fix CTE hazard" in by_id["4787"]["summary"]
    assert "add coverage bin" in by_id["4787"]["summary"]
    assert "Merge" not in by_id["4787"]["summary"]


def test_build_file_tis_empty_commits(monkeypatch):
    """No commits → no TIs, cleanly (guards against divide-by-zero-style bugs)."""
    monkeypatch.setattr(C, "build_file_commits",
                        lambda repo, rel, follow=True, force=False: {
                            "commits": [], "n_commits": 0, "path": "x",
                            "repo": "R", "head": "H", "follow": True,
                            "truncated": False})
    class _R: key="R"; head="H"
    d = C.build_file_tis(_R(), "x")
    assert d["n_tis"] == 0 and d["tis"] == [] and d["n_commits"] == 0


def test_turnin_regex_rejects_workweek_and_paths():
    """B2: workweek stamps like 24ww32 and bare path numbers must not be
    mistaken for turnin ids."""
    ids = [m.group(1) for m in C._TURNIN_RE.finditer(
        "24ww32 gk.workarea.01 v2 build 999 user_turnin4787")]
    assert ids == ["4787"]


def test_list_file_commits_uses_follow(monkeypatch):
    """D1: list_file_commits must pass --follow when requested (rename
    history is essential for file identity across renames)."""
    called = {}
    class _R:
        root = "/tmp"; key = "K"; base = ""
        def to_git_path(self, r): return r
        def _git(self, *args, timeout=60):
            called["args"] = args
            import types
            return types.SimpleNamespace(returncode=0,
                                          stdout="dead\nbeef\n", stderr="")
    out = C.list_file_commits(_R(), "x", follow=True)
    assert out == ["dead", "beef"]
    assert "--follow" in called["args"]
    assert "--no-merges" in called["args"]

    called.clear()
    C.list_file_commits(_R(), "x", follow=False)
    assert "--follow" not in called["args"]


# --- validate_model_root ---------------------------------------------------
def test_validate_model_root_missing(tmp_path):
    v = C.validate_model_root("")
    assert not v["ok"] and "empty" in v["error"]
    v = C.validate_model_root(str(tmp_path / "nope"))
    assert not v["ok"] and "not a directory" in v["error"]


def test_validate_model_root_not_a_git_repo(tmp_path):
    v = C.validate_model_root(str(tmp_path))
    assert not v["ok"] and "not a git repo" in v["error"]


def test_validate_model_root_git_but_no_intel_config(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    v = C.validate_model_root(str(tmp_path))
    assert not v["ok"]
    assert "not a MODEL_ROOT" in v["error"]
    assert "intel.cluster" in v["error"]
    assert "hdk.rc" in v["error"]  # actionable — points at the source command


def test_validate_model_root_subdir_rejected(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sub = tmp_path / "core"; sub.mkdir()
    v = C.validate_model_root(str(sub))
    assert not v["ok"] and "not its toplevel" in v["error"]
    assert v["root"]  # tells the user WHICH toplevel to pass instead


def test_validate_model_root_ok(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, val in (("intel.cluster", "fit"), ("intel.stepping", "jnc-a0"),
                   ("user.email", "t@e"), ("user.name", "t")):
        sp.run(["git", "-C", str(tmp_path), "config", k, val], check=True)
    # need at least one commit for HEAD-name to resolve
    (tmp_path / "f").write_text("x")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-qm", "x"], check=True)
    v = C.validate_model_root(str(tmp_path))
    assert v["ok"] and v["cluster"] == "fit" and v["stepping"] == "jnc-a0"
    assert v["label"].startswith("fit-jnc-a0-")


# --- RepoInfo.with_base ----------------------------------------------------
def test_with_base_switches_and_validates(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "fe").mkdir()
    (tmp_path / "core" / "fe" / "cte").mkdir()
    (tmp_path / "rtl").mkdir()
    r = C.RepoInfo("K", str(tmp_path))
    # default base auto-detected to "core/fe"
    assert r.ok and r.base == "core/fe"
    r2 = r.with_base("core/fe/cte")
    assert r2.base == "core/fe/cte" and r2.root == r.root
    # Original unchanged (shallow copy).
    assert r.base == "core/fe"
    # Sibling roots also work.
    assert r.with_base("rtl").base == "rtl"
    # Empty means the toplevel.
    assert r.with_base("").base == ""
    # No-op when equal.
    assert r.with_base("core/fe") is r
    # Nonexistent path rejected.
    import pytest as _pt
    with _pt.raises(ValueError, match="does not exist"):
        r.with_base("does/not/exist")
    # Traversal rejected.
    with _pt.raises(ValueError, match="escapes"):
        r.with_base("../outside")


# --- HEAD-aware cache keys -------------------------------------------------
def _fake_repo(key="R", head="abc1234"):
    class _R:
        pass
    r = _R()
    r.key = key
    r.head = head
    return r


def test_ck_includes_head(monkeypatch):
    r = _fake_repo("JNC", "abc1234")
    assert C._ck(r, "sha") == "JNC@abc1234_sha"
    r2 = _fake_repo("JNC", "def5678")
    assert C._ck(r2, "sha") != C._ck(r, "sha")
    # No HEAD -> stable placeholder (doesn't collide with a real head).
    assert C._ck(_fake_repo("JNC", ""), "sha") == "JNC@nohead_sha"


def test_prune_stale_cache_removes_wrong_head_and_legacy(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(C, "CACHE_DIR", tmp_path)
    # Live entry
    (tmp_path / "commit__JNC@aaa111_sha1.json").write_text("{}")
    # Stale entry — HEAD moved
    (tmp_path / "commit__JNC@bbb222_sha1.json").write_text("{}")
    # Stale entry — different repo, no longer registered
    (tmp_path / "ti__GONE@aaa111_1234.json").write_text("{}")
    # Legacy pre-HEAD-aware entry (no '@' in the key)
    (tmp_path / "commit__CUSTOM_core-jnc-a0-master_sha1.json").write_text("{}")
    n = C._prune_stale_cache(["JNC"], {"JNC": "aaa111"})
    survived = sorted(p.name for p in tmp_path.iterdir())
    assert survived == ["commit__JNC@aaa111_sha1.json"]
    assert n == 3


# --- effective_turnins -----------------------------------------------------
def test_effective_turnins_non_merge_uses_intro():
    """A non-merge bug-fix commit has no intrinsic turnin id in its subject —
    the ancestry-path introducing merge is the correct owner."""
    info = {"is_merge": False, "turnins": [], "intro_turnins": ["7931"]}
    assert C.effective_turnins(info) == ["7931"]


def test_effective_turnins_non_merge_intrinsic_wins():
    """If a non-merge commit's own subject somehow carries a turnin id, use
    that (intrinsic beats intro)."""
    info = {"is_merge": False, "turnins": ["9049"], "intro_turnins": ["7931"]}
    assert C.effective_turnins(info) == ["9049"]


def test_effective_turnins_merge_uses_intrinsic_only():
    """The exact regression: a sync-from-master merge with no intrinsic
    turnin must NOT inherit the ancestry-path merge's turnin. Otherwise
    files it never touched appear in that TI's file scope (phantom TI
    21456 on fe_ifu_pp_ref.e)."""
    info = {"is_merge": True, "turnins": [], "intro_turnins": ["21456"]}
    assert C.effective_turnins(info) == []


def test_effective_turnins_merge_with_own_ti_ignores_intro():
    """A merge that IS a legit turnin merge (e.g. integrate_bundle21163)
    uses its own turnin — never falls through to the ancestry-path merge
    (which would double-count)."""
    info = {"is_merge": True, "turnins": ["21163"], "intro_turnins": ["21456"]}
    assert C.effective_turnins(info) == ["21163"]
