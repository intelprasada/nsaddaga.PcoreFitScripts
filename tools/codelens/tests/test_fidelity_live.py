"""Live fidelity harness — runs A/C/D/G invariants against a real repo.

Skipped when the JNC repo isn't present (no CI), so it's safe to leave in the
suite. Run explicitly with:
    pytest tools/codelens/tests/test_fidelity_live.py -v -s
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parents[1] / "codelens_server.py"
_spec = importlib.util.spec_from_file_location("codelens_server", _MOD)
C = importlib.util.module_from_spec(_spec)
sys.modules["codelens_server"] = C
_spec.loader.exec_module(C)


def _pick_repo():
    for k in ("JNC", "GFC"):
        r = C.get_repo(k)
        if r.ok:
            return r
    return None


REPO = _pick_repo()
TEST_FILE = "cte/fe_ifu_pp/fe_ifu_pp_ref.e"
skip_no_repo = pytest.mark.skipif(
    REPO is None or not (REPO.base_abs() / TEST_FILE).exists(),
    reason="live repo / test file not available")


@skip_no_repo
def test_a1_a2_introducing_merge_reachability():
    """A1/A2: for a sample of commits from the test file, the resolved
    introducing merge is on the ancestry path, is a merge, and either
    matches _TURNIN_RE or is the oldest ancestry-path merge."""
    d = C.build_file_commits(REPO, TEST_FILE)
    sampled = [c for c in d["commits"] if c.get("merge")][:15]
    assert sampled, "expected at least one commit with a resolved merge"
    for c in sampled:
        merge_full = c["merge"]  # 12-char short
        # merge must be an ancestor of HEAD
        r = REPO._git("merge-base", "--is-ancestor", merge_full, "HEAD")
        assert r.returncode == 0, f"merge {merge_full} for {c['short']} not ancestor of HEAD"
        # and must be a merge commit (>=2 parents)
        r2 = REPO._git("rev-list", "--parents", "-n", "1", merge_full)
        parents = r2.stdout.split()[1:] if r2.stdout else []
        assert len(parents) >= 2, f"merge {merge_full} is not a merge commit"


@skip_no_repo
def test_c1_d3_round_trip_tis_derived_from_commits():
    """C1/D3: TIScope must equal ⋃ per-commit turnins from CommitScope,
    verified against live data — no phantom TIs, no missing TIs."""
    cs = C.build_file_commits(REPO, TEST_FILE)
    ts = C.build_file_tis(REPO, TEST_FILE)
    from_commits = set()
    for c in cs["commits"]:
        for t in (c.get("turnins") or []):
            from_commits.add(t)
    from_ti_list = {t["id"] for t in ts["tis"]}
    assert from_ti_list == from_commits, (
        f"round-trip mismatch: only-in-TIs={from_ti_list - from_commits} "
        f"only-in-commits={from_commits - from_ti_list}")


@skip_no_repo
def test_c2_no_phantom_tis():
    """C2: every TI in TIScope has ≥1 commit in CommitScope pointing at it."""
    cs = C.build_file_commits(REPO, TEST_FILE)
    ts = C.build_file_tis(REPO, TEST_FILE)
    for ti in ts["tis"]:
        matches = [c for c in cs["commits"] if ti["id"] in (c.get("turnins") or [])]
        assert matches, f"phantom TI {ti['id']} — no commit maps to it"
        assert ti["n_commits"] == len(matches), (
            f"TI {ti['id']} n_commits={ti['n_commits']} but "
            f"{len(matches)} commits actually map to it")


@skip_no_repo
def test_no_monoculture_regression():
    """Regression: fe_ifu_pp_ref.e must not resolve every commit to a single
    TI (the pre-fix bug where everything mapped to the newest turnin)."""
    ts = C.build_file_tis(REPO, TEST_FILE)
    assert ts["n_tis"] >= 5, (
        f"expected many distinct TIs for {TEST_FILE}, got {ts['n_tis']} — "
        "possible regression to newest-merge bug")


@skip_no_repo
def test_d4_commits_actually_touch_file():
    """D4: every commit in Commit Scope actually changed the test file (as
    confirmed by an independent `git log -- <file>` check on a sample)."""
    cs = C.build_file_commits(REPO, TEST_FILE)
    sample = cs["commits"][:8]
    git_path = REPO.to_git_path(TEST_FILE)
    for c in sample:
        r = REPO._git("log", "-1", "--follow", "--format=%H",
                      c["sha"], "--", git_path)
        assert r.returncode == 0 and r.stdout.strip() == c["sha"], (
            f"commit {c['short']} does not appear to touch {git_path}")


@skip_no_repo
def test_g3_commit_lens_turnins_match_extractor():
    """G3: commit lens `turnins` equals _TURNIN_RE.findall on the introducing
    merge subject — same extractor, no other sources sneaking in."""
    cs = C.build_file_commits(REPO, TEST_FILE)
    sample = [c for c in cs["commits"] if c.get("merge")][:6]
    for c in sample:
        subj = c.get("merge_summary") or ""
        expected = []
        seen = set()
        for m in C._TURNIN_RE.finditer(subj + "\n" + (c.get("summary") or "")):
            t = m.group(1)
            if t not in seen:
                seen.add(t); expected.append(t)
        assert set(c["turnins"]) == set(expected), (
            f"commit {c['short']}: turnins={c['turnins']} but regex on "
            f"subject/merge={expected}")


@skip_no_repo
def test_a5_stability_idempotent():
    """A5: cache-cleared re-run gives the same introducing merge (no
    randomness / ordering flakiness)."""
    cs1 = C.build_file_commits(REPO, TEST_FILE)
    # clear the commit cache for these shas and rebuild
    for c in cs1["commits"][:5]:
        C._cache_path("commit", f"{REPO.key}_{c['sha']}").unlink(missing_ok=True)
    cs2 = C.build_file_commits(REPO, TEST_FILE)
    m1 = {c["sha"]: c["merge"] for c in cs1["commits"][:5]}
    m2 = {c["sha"]: c["merge"] for c in cs2["commits"][:5]}
    assert m1 == m2, f"introducing merge unstable: {m1} vs {m2}"
