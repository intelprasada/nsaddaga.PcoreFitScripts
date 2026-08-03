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
