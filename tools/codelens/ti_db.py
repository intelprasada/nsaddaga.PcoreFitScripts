"""TiDb — authoritative Turnin index built from `turnininfo`.

Design objective: replace the fragile "parse merge subject → guess TI id"
approach with authoritative Gatekeeper data. For each (cluster, stepping,
branch) triple we shell out to `turnininfo -all -format json` once,
persist the result, and expose three lookups every downstream endpoint
needs:

    tis_for_file(path)     — TI Scope for a file
    ti(id)                 — TI Lens payload
    attribute_shas(shas, path) — sha → TI id (or None = "orphan")

Correctness invariants (tested):
  * Only TIs with status ∈ {released, accepted} AND a non-empty
    `bundle_commit` participate — those are the ones that actually landed
    on master.
  * sha → TI is a single-owner mapping: if a sha shows up in multiple
    TIs' bundle_commit^1..^2 ranges (which should never happen for a
    linear master history), oldest-bundle-wins.
  * TIs whose `bundle_commit` is unreachable in the current workarea
    (workarea is on a different branch or older than the TI) are kept in
    `by_id` / `by_file` but contribute nothing to sha attribution.

Storage layout: ~/.codelens/ti_db/{cluster}_{stepping}_{branch}.json
holds the parsed compact record set + an `as_of_epoch` header. The
sha→ti_id map is NOT persisted; it's cheap to rebuild lazily per-file
because we only need it for TIs that touched THAT file.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TURNININFO = os.environ.get(
    "CODELENS_TURNININFO",
    "/p/hdk/rtl/proj_tools/proj_binx/xhdk74_sles15/latest/turnininfo")

_STORE = Path(os.environ.get("CODELENS_TIDB_STORE")
              or Path.home() / ".codelens" / "ti_db")

# Fields we keep from each turnininfo record — the full record is ~15KB
# per TI (turnin_notes carries verbose git output), so we prune to what
# the TI Lens actually shows.
_KEEP_FIELDS = (
    "id", "bundle_id", "bundle_commit", "cluster", "stepping", "branch",
    "status", "stage", "user", "bugs", "ecos", "comments",
    "completed_time", "completed_time_epoch",
    "turnin_time", "turnin_time_epoch",
    "code_review_url", "code_review_status",
    "files_changed", "user_commit",
)

# TIs in these statuses actually landed on master. Anything else is
# noise from Gatekeeper's history (cancelled/rejected proposals).
_ON_MASTER_STATUSES = {"released", "accepted"}


def _db_key(cluster: str, stepping: str, branch: str) -> str:
    return f"{cluster}_{stepping}_{branch}"


def _db_path(cluster: str, stepping: str, branch: str) -> Path:
    return _STORE / f"{_db_key(cluster, stepping, branch)}.json"


def _prune_record(r: dict) -> dict:
    """Return a stripped copy retaining only fields we surface."""
    out: dict = {}
    for k in _KEEP_FIELDS:
        if k in r:
            out[k] = r[k]
    return out


class TiDb:
    """Per (cluster, stepping, branch) authoritative TI index.

    Not thread-safe for refresh(), but query methods are read-only after
    a refresh completes (they only mutate the in-process sha-map cache,
    which is fine to lose on race — worst case one extra rev-list call).
    """

    def __init__(self, cluster: str, stepping: str, branch: str,
                 repo_root: str, merge_set_fn=None):
        self.cluster = cluster
        self.stepping = stepping
        self.branch = branch
        self.repo_root = repo_root
        # Injected: callable(repo_root, msha) -> frozenset[str]. Kept
        # optional so tests can stub it; production wires in the same
        # helper codelens_server uses so the merge-introduced-set cache
        # is shared.
        self._merge_set = merge_set_fn or self._default_merge_set

        self.by_id: dict[str, dict] = {}
        self.by_file: dict[str, list[str]] = {}
        self.by_bundle_commit: dict[str, str] = {}
        self.as_of_epoch: int = 0
        self.error: str = ""

        # sha -> ti_id, populated lazily per file query. None value means
        # "we've checked this sha against every candidate TI for its file
        # and it's an orphan" (structurally different from "not yet
        # checked").
        self._sha_owner: dict[str, str | None] = {}
        # set of files whose candidate TIs we've fully expanded into
        # _sha_owner. Skip re-expansion.
        self._file_expanded: set[str] = set()

    # ---- persistence -----------------------------------------------------

    def _load_disk(self) -> bool:
        p = _db_path(self.cluster, self.stepping, self.branch)
        if not p.is_file():
            return False
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.error = f"disk load failed: {e}"
            return False
        self.as_of_epoch = int(blob.get("as_of_epoch", 0))
        recs = blob.get("records", [])
        self._install(recs)
        return True

    def _save_disk(self, records: list[dict]) -> None:
        _STORE.mkdir(parents=True, exist_ok=True)
        p = _db_path(self.cluster, self.stepping, self.branch)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "cluster": self.cluster,
            "stepping": self.stepping,
            "branch": self.branch,
            "as_of_epoch": self.as_of_epoch,
            "n_records": len(records),
            "records": records,
        }), encoding="utf-8")
        tmp.replace(p)

    # ---- refresh from turnininfo -----------------------------------------

    def refresh(self, timeout: int = 300) -> None:
        """Pull `turnininfo -all` and rebuild the index."""
        _STORE.mkdir(parents=True, exist_ok=True)
        out_path = _STORE / f"raw_{_db_key(self.cluster, self.stepping, self.branch)}.json"
        args = [TURNININFO, "-c", self.cluster, "-s", self.stepping,
                "-b", self.branch, "-all", "-format", "json",
                "-output", str(out_path)]
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, check=False)
        if r.returncode != 0 or not out_path.is_file():
            self.error = (f"turnininfo failed (rc={r.returncode}): "
                          f"{(r.stderr or r.stdout or '').strip()[:200]}")
            return
        try:
            raw = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.error = f"turnininfo output parse failed: {e}"
            return
        # Keep only on-master TIs and strip verbose fields.
        filtered: list[dict] = []
        for r0 in raw:
            if not isinstance(r0, dict):
                continue
            if not r0.get("bundle_commit"):
                continue
            if r0.get("status") not in _ON_MASTER_STATUSES:
                continue
            filtered.append(_prune_record(r0))
        self.as_of_epoch = int(time.time())
        self.error = ""
        self._install(filtered)
        self._save_disk(filtered)

    def _install(self, records: list[dict]) -> None:
        by_id: dict[str, dict] = {}
        by_file: dict[str, list[str]] = {}
        by_bc: dict[str, str] = {}
        # Sort oldest first so first-in-list wins on any collision, which
        # matches "oldest bundle owns" semantics downstream.
        recs = sorted(records,
                      key=lambda r: (r.get("completed_time_epoch") or 0,
                                     str(r.get("id"))))
        for r in recs:
            tid = str(r.get("id"))
            if not tid or tid == "None":
                continue
            by_id[tid] = r
            bc = r.get("bundle_commit") or ""
            if bc:
                by_bc.setdefault(bc, tid)
            for f in (r.get("files_changed") or []):
                by_file.setdefault(f, []).append(tid)
        self.by_id = by_id
        self.by_file = by_file
        self.by_bundle_commit = by_bc
        # Invalidate lazy caches.
        self._sha_owner.clear()
        self._file_expanded.clear()

    def load_or_refresh(self, stale_after_sec: int = 24 * 3600) -> None:
        """Load from disk if fresh; otherwise refresh."""
        if not self._load_disk():
            self.refresh()
            return
        if not self.as_of_epoch or (time.time() - self.as_of_epoch) > stale_after_sec:
            self.refresh()

    # ---- queries ---------------------------------------------------------

    def ti(self, ti_id) -> dict | None:
        return self.by_id.get(str(ti_id))

    def tis_for_file(self, path: str) -> list[dict]:
        """Return all TI records whose files_changed includes `path`,
        sorted newest bundle first (which matches how the UI wants to
        show recent activity)."""
        ids = self.by_file.get(path, [])
        recs = [self.by_id[i] for i in ids if i in self.by_id]
        recs.sort(key=lambda r: -(r.get("completed_time_epoch") or 0))
        return recs

    def _expand_file(self, path: str) -> None:
        """Populate _sha_owner for every sha reachable from any candidate
        TI's bundle_commit^1..^2 range. Idempotent per file — the caller
        can call this multiple times cheaply."""
        if path in self._file_expanded:
            return
        candidates = self.by_file.get(path, [])
        if not candidates:
            self._file_expanded.add(path)
            return
        # Expand oldest-first so oldest-bundle wins on collision. We
        # already sorted by completed_time_epoch when installing, so
        # iterate in-order.
        ordered = sorted(candidates,
                         key=lambda i: ((self.by_id[i].get("completed_time_epoch") or 0),
                                        i))
        # Parallel: rev-list is I/O-bound. In-process cache in merge_set_fn
        # keeps repeated calls cheap.
        def _fetch(ti_id: str):
            bc = self.by_id[ti_id].get("bundle_commit") or ""
            if not bc:
                return ti_id, frozenset()
            return ti_id, self._merge_set(self.repo_root, bc)
        with ThreadPoolExecutor(max_workers=min(16, max(4, len(ordered)))) as ex:
            sets = list(ex.map(_fetch, ordered))
        for ti_id, sha_set in sets:
            for sha in sha_set:
                # Oldest-wins: skip if already assigned.
                if sha not in self._sha_owner:
                    self._sha_owner[sha] = ti_id
        self._file_expanded.add(path)

    def attribute_shas(self, shas: list[str] | set[str],
                       path: str) -> dict[str, str | None]:
        """Return {sha: ti_id or None} for the given shas relative to
        the candidate TIs of `path`. None means orphan — no TI on file's
        history introduced this sha (typical for shas that came in via
        cross-cluster sync merges outside Gatekeeper's tracking)."""
        self._expand_file(path)
        return {sha: self._sha_owner.get(sha) for sha in shas}

    # ---- default git helper ---------------------------------------------

    @staticmethod
    def _default_merge_set(repo_root: str, msha: str) -> frozenset[str]:
        """Fallback merge-introduced-set impl for standalone use.
        codelens_server injects its cached version at startup."""
        r = subprocess.run(
            ["git", "-C", repo_root, "--no-pager", "rev-list",
             f"{msha}^1..{msha}^2"],
            capture_output=True, text=True, timeout=30, check=False)
        if r.returncode != 0 or not r.stdout:
            return frozenset()
        return frozenset(ln.strip() for ln in r.stdout.splitlines() if ln.strip())

    # ---- header for UI ---------------------------------------------------

    def status(self) -> dict:
        return {
            "cluster": self.cluster, "stepping": self.stepping,
            "branch": self.branch,
            "as_of_epoch": self.as_of_epoch,
            "n_tis": len(self.by_id),
            "n_files": len(self.by_file),
            "error": self.error,
        }
