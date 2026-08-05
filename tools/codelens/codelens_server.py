#!/usr/bin/env python3
"""CodeLens — an IDE-like code browser for the GFC / JNC front-end (core/fe).

Left pane: the source file (Monaco editor, SystemVerilog aware).
Right pane: *context for whatever lines are currently on screen* —
  * who last changed each visible line (git blame, author + IDSID),
  * which turnins introduced those lines (user_turnin<N> merges),
  * which HSD article ids the introducing commits reference.

The user sets the "pane size" (how many lines count as on-screen); the frontend
sends that visible [start,end] range to /api/context and /api/blame, and the
right pane recomputes as you scroll.

Everything is derived live from the git repos with stdlib only — no external
packages, mirroring the house style of tools/teamhub.

Environment knobs (all optional):
  CODELENS_REPO   Path anywhere inside the repo to browse. Its git toplevel is
                  auto-detected. Default: the JNC fit "-latest" turnin symlink
                  (what `setJNCfit` resolves to), then a local JNC clone.
  GFC_REPO        Override the GFC repo path (default: gfc-b0 "-latest").
  JNC_REPO        Override the JNC repo path (default: jnc-a0 fit "-latest").
  CODELENS_BASE   Sub-path within the repo to root the browser at.
                  Default: auto-detect "core/fe" (falls back to "fe", then ".").
  CODELENS_PORT   HTTP port (default: 8770).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import glob as _glob
import json
import os
import re
import subprocess
import time
from datetime import datetime
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

HERE = Path(__file__).resolve().parent

# ti_db (sibling module) — authoritative Turnin index from `turnininfo`.
import sys as _sys
if str(HERE) not in _sys.path:
    _sys.path.insert(0, str(HERE))
import ti_db as _ti_db_mod  # noqa: E402


# --------------------------------------------------------------------------
# TiDb registry — one TiDb per (cluster, stepping, branch, repo_root) tuple.
# Populated lazily on first query; refreshable from the UI.
# --------------------------------------------------------------------------
_TIDB_REG: dict[tuple[str, str, str, str], "_ti_db_mod.TiDb"] = {}


def _tidb_for_repo(repo: "RepoInfo", auto_refresh: bool = True):
    """Return a TiDb bound to this repo's cluster/stepping/branch, loading
    from disk (or refreshing if stale/missing) on first access."""
    if not (repo.cluster and repo.stepping and repo.branch):
        return None
    key = (repo.cluster, repo.stepping, repo.branch, repo.root)
    db = _TIDB_REG.get(key)
    if db is None:
        db = _ti_db_mod.TiDb(
            repo.cluster, repo.stepping, repo.branch, repo.root,
            merge_set_fn=lambda root, msha: _merge_introduced_set(
                _RootRepo(root), msha),
        )
        if auto_refresh:
            db.load_or_refresh()
        else:
            db._load_disk()
        _TIDB_REG[key] = db
    return db


class _RootRepo:
    """Adapter to reuse _merge_introduced_set which expects a repo-like
    object with a .root attribute."""
    __slots__ = ("root",)
    def __init__(self, root: str):
        self.root = root


# --------------------------------------------------------------------------
# Repo discovery — mirrors setGFC / setJNCfit resolution (see tools/teamhub).
# --------------------------------------------------------------------------
def _latest_model(glob_pat: str) -> str | None:
    """Newest model bundle matching a glob, sorted by the trailing workweek tag
    (e.g. '26ww15a'). Skips '-defective' bundles and anything without a .git."""
    tag_re = re.compile(r"(\d+ww\d+[a-z]?)(?:\.\d+)?$")
    best: tuple[str, str] | None = None
    for p in _glob.glob(glob_pat):
        name = Path(p).name
        if "defective" in name:
            continue
        m = tag_re.search(name)
        key = m.group(1) if m else name
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


_GFC_LATEST = "/p/hdk/rtl/proj_data/xhdk74/bak_latest_turnins/gfc/core/core-gfc-b0-master-latest"
_JNC_LATEST = "/p/hdk/rtl/proj_data/xhdk74/bak_latest_turnins/jnc/fit/fit-jnc-a0-master-latest"


def _local_clone(*globs: str) -> str | None:
    for g in globs:
        for p in sorted(_glob.glob(g)):
            if (Path(p) / ".git").exists() or (Path(p).parent / ".git").exists():
                return p
    return None


def _default_gfc() -> str | None:
    if Path(_GFC_LATEST, "core", "fe").exists():
        return _GFC_LATEST
    return (_latest_model("/nfs/site/proj/gfc/gfc.models.*/core/core-gfc-b0-master-*")
            or _local_clone("/nfs/site/disks/*/GFC_*/core"))


def _default_jnc() -> str | None:
    if Path(_JNC_LATEST, "core", "fe").exists():
        return _JNC_LATEST
    return (_latest_model("/nfs/site/proj/jnc/jnc.*/fit/fit-jnc-a0-master-*")
            or _local_clone("/nfs/site/disks/*/JNC_*/core"))


# Named, switchable repos surfaced in the UI. Values may be any path inside the
# repo; the toplevel is resolved lazily in RepoInfo.
_REPO_SPECS = {
    "JNC": os.environ.get("JNC_REPO") or _default_jnc(),
    "GFC": os.environ.get("GFC_REPO") or _default_gfc(),
}
# CODELENS_REPO, if set, becomes an extra "custom" repo and the default.
_CUSTOM = os.environ.get("CODELENS_REPO")
if _CUSTOM:
    _REPO_SPECS = {"REPO": _CUSTOM, **_REPO_SPECS}


def _register_custom(key: str, path: str) -> None:
    """Add a user-registered workarea to _REPO_SPECS at runtime."""
    _REPO_SPECS[key] = path
    get_repo.cache_clear()  # RepoInfo is memoized; force re-resolution


def _load_saved_custom_repos() -> None:
    for key, path in _custom_load().items():
        if isinstance(path, str) and path:
            _register_custom(key, path)

BASE_OVERRIDE = os.environ.get("CODELENS_BASE")  # e.g. "core/fe"
PORT = int(os.environ.get("CODELENS_PORT", "8770"))

# --------------------------------------------------------------------------
# User-registered custom workareas (MODEL_ROOT paths from `source hdk.rc
# -model_shell -w <path>`). Persisted so they survive server restarts.
# --------------------------------------------------------------------------
_CUSTOM_STORE = Path(os.environ.get("CODELENS_CUSTOM_STORE")
                     or Path.home() / ".codelens" / "custom_repos.json")


def _custom_load() -> dict:
    try:
        return json.loads(_CUSTOM_STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _custom_save(d: dict) -> None:
    _CUSTOM_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CUSTOM_STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    tmp.replace(_CUSTOM_STORE)


def validate_model_root(raw_path: str) -> dict:
    """Return {ok, error, root, cluster, stepping, branch, label} for a path
    the user claims is a MODEL_ROOT (initialized via
    `source /p/hdk/rtl/hdk.rc ... -model_shell -w <path>`).

    A path qualifies iff:
      * it exists and is a directory,
      * it is a git repo (its `git rev-parse --show-toplevel` succeeds),
      * the user-provided path IS the toplevel (not a subdirectory),
      * `git config --get intel.cluster` and `intel.stepping` are set — these
        are written by the model-shell setup flow and identify the -m and -s
        arguments respectively.
    """
    out = {"ok": False, "error": "", "root": "", "cluster": "",
           "stepping": "", "branch": "", "label": ""}
    if not raw_path:
        out["error"] = "path is empty"
        return out
    p = os.path.expanduser(raw_path.strip())
    if not os.path.isdir(p):
        out["error"] = f"not a directory: {p}"
        return out
    r = subprocess.run(["git", "-C", p, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, timeout=15, check=False)
    if r.returncode != 0:
        out["error"] = "not a git repo (git rev-parse failed)"
        return out
    root = r.stdout.strip()
    if os.path.realpath(root) != os.path.realpath(p):
        out["error"] = (f"path is inside a git repo but not its toplevel; "
                        f"pass the MODEL_ROOT itself: {root}")
        out["root"] = root
        return out
    def _cfg(k):
        rr = subprocess.run(["git", "-C", root, "config", "--get", k],
                            capture_output=True, text=True, timeout=10,
                            check=False)
        return rr.stdout.strip() if rr.returncode == 0 else ""
    cluster, stepping = _cfg("intel.cluster"), _cfg("intel.stepping")
    if not cluster or not stepping:
        out["error"] = ("path is a git repo but not a MODEL_ROOT — "
                        "git config intel.cluster / intel.stepping are unset. "
                        "Did you `source /p/hdk/rtl/hdk.rc ... -model_shell "
                        f"-w {root}` in this workarea first?")
        out["root"] = root
        return out
    br = subprocess.run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True, text=True, timeout=10, check=False)
    branch = br.stdout.strip() if br.returncode == 0 else ""
    out.update(ok=True, root=root, cluster=cluster, stepping=stepping,
               branch=branch, label=f"{cluster}-{stepping}-{branch}"
               if branch else f"{cluster}-{stepping}")
    return out

# Files we surface with syntax highlighting; everything else opens as text.
_LANG_BY_EXT = {
    ".sv": "systemverilog", ".svh": "systemverilog", ".svi": "systemverilog",
    ".svp": "systemverilog", ".v": "systemverilog", ".vh": "systemverilog",
    ".vs": "systemverilog", ".vt": "systemverilog",
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
    ".py": "python", ".pl": "perl", ".pm": "perl", ".tcl": "tcl",
    ".sh": "shell", ".csh": "shell", ".json": "json", ".md": "markdown",
    ".yaml": "yaml", ".yml": "yaml", ".xml": "xml", ".e": "specman",
}
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_RANGE_LINES = 4000

# --------------------------------------------------------------------------
# Persistent cache (per-sha enrichment is immutable, so it lives forever).
# --------------------------------------------------------------------------
CACHE_DIR = HERE / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(kind: str, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:180]
    return CACHE_DIR / f"{kind}__{safe}.json"


def _head_tag(repo: "RepoInfo") -> str:
    """Short HEAD sha for the repo, used as a cache-key component so ancestry-
    derived fields (introducing merge, TI ancestry lookups, per-file history)
    are automatically invalidated when the workarea's HEAD moves — e.g. when a
    user's registered workarea rotates to a newer turnin bundle."""
    return (repo.head or "nohead").replace("/", "_")


def _ck(repo: "RepoInfo", *parts: str) -> str:
    """HEAD-aware cache key: {repo.key}@{head}_{parts...}. Any HEAD movement
    (workarea reset, new turnin bundle) transparently invalidates all keys
    for that repo without needing an explicit purge."""
    tail = "_".join(str(p) for p in parts)
    return f"{repo.key}@{_head_tag(repo)}_{tail}"


def _prune_stale_cache(repo_keys: list[str], head_tags: dict[str, str]) -> int:
    """At startup, remove cache files whose repo-key/head-tag pair no longer
    matches any live repo — old workareas, or entries from a previous head
    that will never be hit again. Returns the number of files removed."""
    removed = 0
    live = {(k, head_tags.get(k, "")) for k in repo_keys if head_tags.get(k)}
    # File name shape: {kind}__{repo.key}@{head}_{tail}.json
    pat = re.compile(r"__([^@]+)@([^_]+)_")
    for p in CACHE_DIR.glob("*.json"):
        m = pat.search(p.name)
        if not m:
            # Legacy pre-HEAD-aware key — safe to remove.
            try:
                p.unlink(); removed += 1
            except OSError:
                pass
            continue
        if (m.group(1), m.group(2)) not in live:
            try:
                p.unlink(); removed += 1
            except OSError:
                pass
    return removed


def _cache_read(kind: str, key: str):
    p = _cache_path(kind, key)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# In-process cache: merge_sha -> frozenset(shas M brought in via its second
# parent). Populated on first access with a single `git rev-list M^1..M^2`
# call. Keyed by (repo.root, msha). Files with many blame shas share the
# same candidate merges, so this cache turns O(shas × merges) subprocess
# spawns into O(unique merges) — a big win when the on-disk enrichment
# cache is cold.
_MERGE_INTRO_CACHE: dict[tuple[str, str], frozenset[str]] = {}

def _merge_introduced_set(repo: "RepoInfo", msha: str) -> frozenset[str]:
    key = (repo.root, msha)
    hit = _MERGE_INTRO_CACHE.get(key)
    if hit is not None:
        return hit
    r = subprocess.run(
        ["git", "-C", repo.root, "--no-pager", "rev-list",
         f"{msha}^1..{msha}^2"],
        capture_output=True, text=True, timeout=30, check=False)
    shas: frozenset[str]
    if r.returncode == 0 and r.stdout:
        shas = frozenset(ln.strip() for ln in r.stdout.splitlines() if ln.strip())
    else:
        shas = frozenset()
    # Bound memory: cap at 8k entries, LRU-ish (dict insertion order).
    if len(_MERGE_INTRO_CACHE) >= 8000:
        try:
            _MERGE_INTRO_CACHE.pop(next(iter(_MERGE_INTRO_CACHE)))
        except StopIteration:
            pass
    _MERGE_INTRO_CACHE[key] = shas
    return shas


def _cache_write(kind: str, key: str, payload) -> None:
    p = _cache_path(kind, key)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Identity resolution (IDSID) — adapted from tools/teamhub. Blame reports names
# as "Last, First"; we normalise to "First Last" for the phonebook/cache.
# --------------------------------------------------------------------------
IDSID_CACHE_PATH = Path(os.environ.get(
    "IDSID_CACHE", str(HERE / ".idsid_cache.json")))
IDSID_HINTS: dict[str, tuple[str, str]] = {
    "Kushwanth Bandanadham": ("gbandana", "12308499"),
    "Yongxi Li": ("yongxili", "12175166"),
    "Edwin Mendez Valverde": ("efmendez", "10656825"),
}


def _load_idsid_cache() -> dict:
    try:
        return json.loads(IDSID_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_idsid_cache(cache: dict) -> None:
    try:
        IDSID_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    except Exception:
        pass


def _normalize_name(name: str) -> str:
    """'Mostovicz, Tsvi' -> 'Tsvi Mostovicz'; leaves 'First Last' untouched."""
    name = name.strip()
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return name


def _idsid_from_email(mail: str) -> str:
    """Intel emails are usually first.last@intel.com — not the IDSID — so we
    only trust a single-token local part (e.g. gbandana@intel.com)."""
    mail = mail.strip().strip("<>")
    local = mail.split("@", 1)[0] if "@" in mail else ""
    if local and "." not in local and local.isascii():
        return local.lower()
    return ""


def _phonebook_lookup(display_name: str) -> tuple[str, str] | None:
    parts = display_name.strip().split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    try:
        out = subprocess.run(
            ["phonebook", "-p", "phonebook", "-c", "BookName", "-c", "IDSID",
             "-c", "WWID", "-d", "BookName", last],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    for line in out.splitlines():
        if "|" not in line or line.lstrip().startswith("BookName"):
            continue
        fields = [f.strip() for f in line.split("|")]
        if len(fields) < 3:
            continue
        book, idsid, wwid = fields[0], fields[1], fields[2]
        if first.lower() in book.lower() and last.lower() in book.lower() and idsid:
            return idsid, wwid
    return None


def resolve_identities(authors: list[tuple[str, str]]) -> dict[str, dict]:
    """authors: list of (raw_name, email). Returns {raw_name: {idsid,wwid,name}}.
    Uses the on-disk cache first, then email, hints, and finally phonebook."""
    cache = _load_idsid_cache()
    changed = False
    out: dict[str, dict] = {}
    for raw, mail in authors:
        if raw in out:
            continue
        norm = _normalize_name(raw)
        entry = cache.get(norm) or cache.get(raw)
        if entry and entry.get("idsid"):
            out[raw] = {"idsid": entry["idsid"], "wwid": entry.get("wwid", ""),
                        "name": norm}
            continue
        idsid = _idsid_from_email(mail)
        wwid = ""
        if not idsid and norm in IDSID_HINTS:
            idsid, wwid = IDSID_HINTS[norm]
        if not idsid:
            pb = _phonebook_lookup(norm)
            if pb:
                idsid, wwid = pb
        cache[norm] = {"idsid": idsid, "wwid": wwid,
                       "source": "email" if idsid else "unresolved"}
        changed = True
        out[raw] = {"idsid": idsid, "wwid": wwid, "name": norm}
    if changed:
        _save_idsid_cache(cache)
    return out


# --------------------------------------------------------------------------
# RepoInfo — resolves toplevel + base sub-path for a configured repo path.
# --------------------------------------------------------------------------
class RepoInfo:
    def __init__(self, key: str, spec: str | None):
        self.key = key
        self.spec = spec or ""
        self.root = ""
        self.base = ""          # base sub-path relative to root, e.g. "core/fe"
        self.head = ""
        self.cluster = ""       # git config intel.cluster (e.g. "core")
        self.stepping = ""      # git config intel.stepping (e.g. "jnc-a0")
        self.branch = ""        # current branch (usually "master")
        self.error = ""
        self._resolve()

    def _git(self, *args, timeout=30) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", self.root, "--no-pager", *args],
                              capture_output=True, text=True,
                              timeout=timeout, check=False)

    def _resolve(self) -> None:
        if not self.spec:
            self.error = "not configured"
            return
        if not os.path.exists(self.spec):
            self.error = f"path not found: {self.spec}"
            return
        try:
            r = subprocess.run(["git", "-C", self.spec, "rev-parse",
                                "--show-toplevel"], capture_output=True,
                               text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.error = f"git error: {e}"
            return
        if r.returncode != 0:
            self.error = (r.stderr or "not a git repo").strip()
            return
        self.root = r.stdout.strip()
        if BASE_OVERRIDE is not None:
            self.base = BASE_OVERRIDE.strip("/")
        else:
            for cand in ("core/fe", "fe", ""):
                if (Path(self.root) / cand).is_dir():
                    self.base = cand
                    break
        h = self._git("rev-parse", "--short", "HEAD", timeout=15)
        self.head = h.stdout.strip() if h.returncode == 0 else ""
        # Gatekeeper coords for TiDb — written into git config by the
        # `source hdk.rc -model_shell` flow.
        for k, attr in (("intel.cluster", "cluster"),
                        ("intel.stepping", "stepping")):
            r = self._git("config", "--get", k, timeout=10)
            if r.returncode == 0:
                setattr(self, attr, r.stdout.strip())
        br = self._git("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        if br.returncode == 0:
            self.branch = br.stdout.strip()

    @property
    def ok(self) -> bool:
        return bool(self.root) and not self.error

    def base_abs(self) -> Path:
        return Path(self.root) / self.base if self.base else Path(self.root)

    def to_git_path(self, rel: str) -> str:
        """API path (relative to base) -> toplevel-relative path for git."""
        rel = rel.strip("/")
        return f"{self.base}/{rel}" if self.base else rel

    def to_fs_path(self, rel: str) -> Path:
        return (self.base_abs() / rel.strip("/")).resolve()

    def safe_fs_path(self, rel: str) -> Path | None:
        """Resolve rel under base, rejecting path traversal / symlink escapes."""
        base = self.base_abs().resolve()
        target = self.to_fs_path(rel)
        try:
            target.relative_to(base)
        except ValueError:
            return None
        return target

    def as_dict(self) -> dict:
        return {"key": self.key, "root": self.root, "base": self.base,
                "head": self.head, "ok": self.ok, "error": self.error,
                "label": Path(self.root).name if self.root else self.spec}

    def with_base(self, new_base: str | None) -> "RepoInfo":
        """Return a shallow-copy of self rooted at ``new_base`` (relative to
        the repo toplevel). Validates that the resulting directory exists
        inside the repo. Empty / None ``new_base`` means "browse from the repo
        toplevel". Path-traversal escapes (``..``) are rejected.
        """
        if new_base is None:
            return self
        cand = new_base.strip().strip("/")
        if cand == self.base:
            return self
        # Compute target and check it resolves under the repo root.
        root_r = Path(self.root).resolve()
        tgt = (Path(self.root) / cand).resolve() if cand else root_r
        try:
            tgt.relative_to(root_r)
        except ValueError as exc:
            raise ValueError(f"base '{cand}' escapes the repo root") from exc
        if not tgt.is_dir():
            raise ValueError(f"base '{cand}' does not exist in {self.root}")
        import copy as _copy
        new = _copy.copy(self)
        new.base = cand
        return new


@lru_cache(maxsize=8)
def get_repo(key: str) -> RepoInfo:
    return RepoInfo(key, _REPO_SPECS.get(key))


def default_repo_key() -> str:
    for k in _REPO_SPECS:
        if get_repo(k).ok:
            return k
    return next(iter(_REPO_SPECS), "JNC")


# --------------------------------------------------------------------------
# Filesystem tree + file content
# --------------------------------------------------------------------------
_TREE_SKIP = {".git", "__pycache__", ".cache", "node_modules"}


def list_tree(repo: RepoInfo, rel: str) -> dict:
    target = repo.safe_fs_path(rel) if rel else repo.base_abs()
    if target is None or not target.is_dir():
        return {"error": "not a directory", "path": rel, "entries": []}
    dirs, files = [], []
    try:
        for e in os.scandir(target):
            if e.name.startswith(".") or e.name in _TREE_SKIP:
                continue
            child_rel = f"{rel}/{e.name}".strip("/") if rel else e.name
            item = {"name": e.name, "path": child_rel}
            if e.is_dir(follow_symlinks=False):
                item["type"] = "dir"
                dirs.append(item)
            elif e.is_file(follow_symlinks=False):
                item["type"] = "file"
                item["lang"] = _LANG_BY_EXT.get(
                    Path(e.name).suffix.lower(), "plaintext")
                files.append(item)
    except OSError as ex:
        return {"error": str(ex), "path": rel, "entries": []}
    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())
    return {"path": rel, "entries": dirs + files}


def search_files(repo: RepoInfo, query: str, limit: int = 200) -> dict:
    """Fuzzy-ish substring file search under the repo base, via `git ls-files`.

    Query is case-insensitive. Matches on either the basename or the full
    relative path. Basename hits rank above path-only hits. Results are
    capped at `limit`.
    """
    q = (query or "").strip().lower()
    if not q:
        return {"query": query, "entries": [], "truncated": False}
    # git ls-files honors sparse-checkout and .gitignore; runs at repo root.
    # We restrict to the current base via pathspec.
    args = ["ls-files", "-z"]
    if repo.base:
        args += ["--", repo.base]
    r = repo._git(*args, timeout=20)
    if r.returncode != 0:
        return {"error": r.stderr.strip() or "git ls-files failed",
                "query": query, "entries": []}
    base_prefix = (repo.base.rstrip("/") + "/") if repo.base else ""
    hits_name: list[dict] = []
    hits_path: list[dict] = []
    for full in r.stdout.split("\x00"):
        if not full:
            continue
        # Strip the base prefix so `path` is relative to the browser root.
        rel = full[len(base_prefix):] if base_prefix and full.startswith(base_prefix) else full
        name = rel.rsplit("/", 1)[-1]
        nl = name.lower(); pl = rel.lower()
        if q in nl:
            hits_name.append({"name": name, "path": rel, "type": "file",
                              "lang": _LANG_BY_EXT.get(Path(name).suffix.lower(),
                                                       "plaintext")})
        elif q in pl:
            hits_path.append({"name": name, "path": rel, "type": "file",
                              "lang": _LANG_BY_EXT.get(Path(name).suffix.lower(),
                                                       "plaintext")})
        if len(hits_name) + len(hits_path) >= limit * 4:
            break
    hits_name.sort(key=lambda x: (len(x["name"]), x["name"].lower()))
    hits_path.sort(key=lambda x: (len(x["path"]), x["path"].lower()))
    entries = (hits_name + hits_path)[:limit]
    total = len(hits_name) + len(hits_path)
    return {"query": query, "entries": entries,
            "n": len(entries), "total": total,
            "truncated": total > len(entries)}


def read_file(repo: RepoInfo, rel: str) -> dict:
    target = repo.safe_fs_path(rel)
    if target is None or not target.is_file():
        return {"error": "file not found", "path": rel}
    try:
        data = target.read_bytes()
    except OSError as ex:
        return {"error": str(ex), "path": rel}
    truncated = len(data) > _MAX_FILE_BYTES
    if truncated:
        data = data[:_MAX_FILE_BYTES]
    text = data.decode("utf-8", errors="replace")
    lang = _LANG_BY_EXT.get(target.suffix.lower(), "plaintext")
    return {"path": rel, "lang": lang, "truncated": truncated,
            "lines": text.count("\n") + 1, "content": text}


# --------------------------------------------------------------------------
# git blame (per visible line range)
# --------------------------------------------------------------------------
def _clamp_range(start: int, end: int) -> tuple[int, int]:
    start = max(1, start)
    end = max(start, end)
    if end - start + 1 > _MAX_RANGE_LINES:
        end = start + _MAX_RANGE_LINES - 1
    return start, end


def blame_range(repo: RepoInfo, rel: str, start: int, end: int) -> dict:
    gp = repo.to_git_path(rel)
    start, end = _clamp_range(start, end)
    r = subprocess.run(
        ["git", "-C", repo.root, "--no-pager", "blame", "--line-porcelain",
         "-L", f"{start},{end}", "HEAD", "--", gp],
        capture_output=True, text=True, timeout=60, check=False)
    if r.returncode != 0:
        return {"error": (r.stderr or "blame failed").strip(),
                "path": rel, "start": start, "end": end, "lines": []}
    lines = _parse_porcelain(r.stdout, start)
    return {"path": rel, "start": start, "end": end, "lines": lines}


def _parse_porcelain(out: str, start: int) -> list[dict]:
    """Parse `git blame --line-porcelain` into per-line dicts."""
    lines: list[dict] = []
    cur: dict = {}
    lineno = start
    header_re = re.compile(r"^([0-9a-f]{40})\s+\d+\s+(\d+)")
    for raw in out.split("\n"):
        m = header_re.match(raw)
        if m:
            cur = {"sha": m.group(1)}
            continue
        if raw.startswith("author "):
            cur["author"] = raw[len("author "):]
        elif raw.startswith("author-mail "):
            cur["mail"] = raw[len("author-mail "):].strip("<>")
        elif raw.startswith("author-time "):
            cur["time"] = int(raw[len("author-time "):] or 0)
        elif raw.startswith("summary "):
            cur["summary"] = raw[len("summary "):]
        elif raw.startswith("\t"):  # the actual source line — closes the record
            lines.append({
                "line": lineno,
                "sha": cur.get("sha", ""),
                "short": cur.get("sha", "")[:12],
                "author": cur.get("author", ""),
                "mail": cur.get("mail", ""),
                "time": cur.get("time", 0),
                "summary": cur.get("summary", ""),
            })
            lineno += 1
    return lines


# --------------------------------------------------------------------------
# Per-commit enrichment: turnin (introducing merge) + HSD refs. Immutable per
# sha, so cached on disk forever.
# --------------------------------------------------------------------------
_HSD_RE = re.compile(r"\b(\d{9,14})\b")
_TURNIN_RE = re.compile(r"(?:user_turnin|integrate_bundle|turnin)[ _]?(\d{2,7})",
                        re.IGNORECASE)


def _extract_hsds(text: str) -> list[str]:
    seen, out = set(), []
    for m in _HSD_RE.finditer(text or ""):
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def enrich_commit(repo: RepoInfo, sha: str, *, skip_intro: bool = False) -> dict:
    """Return metadata + turnin attribution for `sha`.

    When `skip_intro=True`, the caller commits to computing the ancestry-path
    introducing merge itself in a batched pass (see resolve_intro_merges).
    That skips the per-sha `git log --merges --ancestry-path sha..HEAD` call,
    which scales with distance to HEAD and is the dominant cost when many
    blame shas share the same file. skip_intro=True results are NOT cached
    on disk (they're incomplete); the caller must fill in intro fields
    before persisting via _finalize_intro().
    """
    if not skip_intro:
        ck = _ck(repo, sha)
        cached = _cache_read("commit", ck)
        if cached is not None:
            return cached
    info: dict = {"sha": sha, "short": sha[:12], "author": "", "mail": "",
                  "time": 0, "summary": "", "turnins": [], "hsds": [],
                  "intro_turnins": []}
    meta = subprocess.run(
        ["git", "-C", repo.root, "--no-pager", "show", "-s",
         "--format=%an%x00%ae%x00%at%x00%P%x00%s%x00%b", sha],
        capture_output=True, text=True, timeout=30, check=False)
    is_merge = False
    if meta.returncode == 0 and meta.stdout:
        parts = meta.stdout.split("\x00")
        if len(parts) >= 6:
            info["author"] = parts[0]
            info["mail"] = parts[1].strip("<>")
            info["time"] = int(parts[2] or 0)
            is_merge = len((parts[3] or "").split()) >= 2
            info["summary"] = parts[4]
            body = parts[5]
            msg = f"{parts[4]}\n{body}"
            info["hsds"] = _extract_hsds(msg)
            for m in _TURNIN_RE.finditer(msg):
                t = m.group(1)
                if t not in info["turnins"]:
                    info["turnins"].append(t)
    info["is_merge"] = is_merge
    if skip_intro:
        return info
    # Fallback path (single-shot callers e.g. /api/commit lens): enumerate the
    # ancestry-path merges from sha..HEAD ourselves. This is O(distance-to-
    # HEAD) per call and is intentionally NOT used by build_context /
    # build_file_commits — those use the batched resolve_intro_merges().
    _apply_ancestry_intro(repo, sha, info)
    _cache_write("commit", ck, info)
    return info


def _apply_ancestry_intro(repo: RepoInfo, sha: str, info: dict) -> None:
    """Populate info['merge'/'merge_summary'/'intro_turnins'/'hsds'] by
    walking sha..HEAD merges. See enrich_commit docstring for context."""
    mrg = subprocess.run(
        ["git", "-C", repo.root, "--no-pager", "log", "--merges",
         "--ancestry-path", "--reverse",
         "--format=%H%x00%s", f"{sha}..HEAD"],
        capture_output=True, text=True, timeout=60, check=False)
    if mrg.returncode != 0 or not mrg.stdout:
        return
    for ln in mrg.stdout.splitlines():
        if not ln:
            continue
        msha, _, msubj = ln.partition("\x00")
        if not _TURNIN_RE.search(msubj):
            continue
        if sha not in _merge_introduced_set(repo, msha):
            continue
        info["merge"] = msha[:12]
        info["merge_summary"] = msubj
        for m in _TURNIN_RE.finditer(msubj):
            t = m.group(1)
            if t not in info["intro_turnins"]:
                info["intro_turnins"].append(t)
        for h in _extract_hsds(msubj):
            if h not in info["hsds"]:
                info["hsds"].append(h)
        return


def resolve_intro_merges(repo: RepoInfo, shas: list[str],
                         path: str) -> dict[str, dict]:
    """Batched intro-merge resolver, keyed by sha.

    Uses `git log HEAD --first-parent --merges -- <path>` to get the SHORT
    list of merges that landed changes to this file on master. Only those
    merges could possibly have introduced our blame shas. For each such
    merge M, materialize its introduced-sha set once (M^1..M^2), then
    scan oldest-first and assign each unassigned sha to the first merge
    whose set contains it. Cost: O(file-touching-merges), independent of
    the number of blame shas.

    Returns { sha: {merge, merge_summary, intro_turnins, hsds} }. Shas
    that no first-parent merge introduces are absent from the map (their
    caller should leave intro fields empty).
    """
    if not shas:
        return {}
    git_path = repo.to_git_path(path) if path else ""
    args = ["git", "-C", repo.root, "--no-pager", "log", "HEAD",
            "--first-parent", "--merges", "--format=%H%x00%s"]
    if git_path:
        args += ["--", git_path]
    r = subprocess.run(args, capture_output=True, text=True,
                       timeout=60, check=False)
    if r.returncode != 0 or not r.stdout:
        return {}
    # Newest-first from git log; reverse for oldest-first assignment.
    merges = [ln for ln in r.stdout.splitlines() if ln]
    merges.reverse()
    todo = set(shas)
    out: dict[str, dict] = {}
    for ln in merges:
        if not todo:
            break
        msha, _, msubj = ln.partition("\x00")
        if not _TURNIN_RE.search(msubj):
            continue
        introduced = _merge_introduced_set(repo, msha)
        if not introduced:
            continue
        hit = todo & introduced
        if not hit:
            continue
        intro_ti = []
        for m in _TURNIN_RE.finditer(msubj):
            t = m.group(1)
            if t not in intro_ti:
                intro_ti.append(t)
        intro_hsds = _extract_hsds(msubj)
        for s in hit:
            out[s] = {"merge": msha[:12], "merge_summary": msubj,
                      "intro_turnins": intro_ti, "hsds": intro_hsds}
        todo -= hit
    return out


def _apply_intro_from_map(info: dict, intro: dict) -> None:
    info["merge"] = intro["merge"]
    info["merge_summary"] = intro["merge_summary"]
    for t in intro["intro_turnins"]:
        if t not in info["intro_turnins"]:
            info["intro_turnins"].append(t)
    for h in intro["hsds"]:
        if h not in info["hsds"]:
            info["hsds"].append(h)





def effective_turnins(info: dict) -> list[str]:
    """Return the TIs that legitimately own this commit's changes.

    Rule: if the commit is itself a MERGE, its OWN turnin-tagged subject (if
    any) is authoritative — we deliberately do NOT fall through to the
    ancestry-path introducing merge. Otherwise (non-merge commit), the
    ancestry-path introducing merge is the correct owner: it's the turnin
    that landed this commit on master.

    This split is what prevents the "phantom TI 21456 on fe_ifu_pp_ref.e"
    class of false positives: a sync-from-master merge with no intrinsic
    turnin id used to inherit the *unrelated* integrate_bundle21456 label
    from its ancestry-path merge, causing files it never touched to appear
    under TI 21456's file scope.
    """
    if info.get("is_merge"):
        return list(info.get("turnins") or [])
    # Non-merge: intrinsic wins if present, otherwise fall back to intro.
    return list(info.get("turnins") or info.get("intro_turnins") or [])


def build_context(repo: RepoInfo, rel: str, start: int, end: int,
                  force: bool = False) -> dict:
    """The right-pane payload: aggregate the commits that touch the visible
    range into authors / turnins / HSDs, plus a per-commit line-count."""
    bl = blame_range(repo, rel, start, end)
    if bl.get("error"):
        return {"error": bl["error"], "path": rel, "start": start, "end": end}
    start, end = bl["start"], bl["end"]
    line_by_sha: dict[str, int] = {}
    order: list[str] = []
    for ln in bl["lines"]:
        sha = ln["sha"]
        if not sha:
            continue
        if sha not in line_by_sha:
            line_by_sha[sha] = 0
            order.append(sha)
        line_by_sha[sha] += 1

    commits, authors_seen = [], {}
    turnins: dict[str, None] = {}
    hsds: dict[str, None] = {}
    if force:
        for sha in order:
            _cache_path("commit", _ck(repo, sha)).unlink(missing_ok=True)
    # Cheap metadata pass (git show) in parallel. Skip the ancestry-path
    # intro lookup — TiDb is authoritative for TI attribution when available.
    infos: dict[str, dict] = {}
    max_workers = min(16, max(4, len(order)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for sha, info in zip(order, ex.map(
                lambda s: enrich_commit(repo, s, skip_intro=True), order)):
            infos[sha] = info

    tidb = _tidb_for_repo(repo)
    git_path_full = repo.to_git_path(rel)
    if tidb is not None and not tidb.error:
        # Authoritative: TiDb.attribute_shas maps each blame sha to the TI
        # whose bundle_commit^1..^2 range contains it — falsifiable and
        # rooted in Gatekeeper metadata, not in guess-parsed merge subjects.
        sha2ti = tidb.attribute_shas(order, git_path_full)
    else:
        # Fallback (workarea without model-shell config, or TiDb unavailable):
        # use the git-side ancestry-path picker.
        sha2ti = {}
        intro_map = resolve_intro_merges(repo, order, rel)
        for s in order:
            im = intro_map.get(s)
            if im and im.get("intro_turnins"):
                sha2ti[s] = im["intro_turnins"][0]
    for sha in order:
        info = dict(infos[sha])
        info["lines"] = line_by_sha[sha]
        ti_id = sha2ti.get(sha)
        info["orphan"] = False
        if ti_id:
            info["intro_turnins"] = [ti_id]
            info["merge_summary"] = ""
            if tidb is not None:
                ti_rec = tidb.ti(ti_id)
                if ti_rec:
                    info["merge"] = (ti_rec.get("bundle_commit") or "")[:12]
                    info["merge_summary"] = (
                        (ti_rec.get("comments") or "").strip().split("\n")[0][:120]
                    )
        elif not info["turnins"]:
            info["orphan"] = True
        _cache_write("commit", _ck(repo, sha), info)
        eff = effective_turnins(info)
        info["turnins"] = eff
        commits.append(info)
        authors_seen.setdefault(info["author"], info.get("mail", ""))
        for t in eff:
            turnins.setdefault(t, None)
        for h in info.get("hsds", []):
            hsds.setdefault(h, None)

    idmap = resolve_identities(list(authors_seen.items()))
    authors = []
    for name, mail in authors_seen.items():
        ident = idmap.get(name, {})
        authors.append({
            "name": _normalize_name(name), "raw": name, "mail": mail,
            "idsid": ident.get("idsid", ""), "wwid": ident.get("wwid", ""),
            "lines": sum(c["lines"] for c in commits if c["author"] == name),
        })
    authors.sort(key=lambda a: -a["lines"])
    for c in commits:
        c["idsid"] = idmap.get(c["author"], {}).get("idsid", "")
        c["author"] = _normalize_name(c["author"])
    commits.sort(key=lambda c: -c["lines"])
    return {
        "path": rel, "start": start, "end": end,
        "repo": repo.key, "head": repo.head,
        "line_count": end - start + 1,
        "commit_count": len(commits),
        "authors": authors,
        "turnins": list(turnins),
        "hsds": list(hsds),
        "commits": commits,
    }


# --------------------------------------------------------------------------
# TI lens + Commit lens — per-turnin and per-commit drill-downs. Both are
# derived live from git (fast, always available) and cached immutably: a
# settled turnin's introducing merge and a commit's diff never change.
# --------------------------------------------------------------------------
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's canonical empty tree
_INCOMING_TI_RE = re.compile(r"incoming/[^/]+/(user_turnin\d+)")


def _ti_token_re(tid: str) -> re.Pattern:
    """Match the turnin id as a whole number (not 4787 inside 47870)."""
    return re.compile(rf"(?<!\d){re.escape(str(tid))}(?!\d)")


def _short(sha: str) -> str:
    return sha[:12]


def resolve_turnin_merge(repo: RepoInfo, tid: str) -> str:
    """Map a turnin id to the merge commit that introduced it into the model.

    Turnin merges read like `Merge branch 'master' of …/user_turnin4787`; some
    are also tagged `*_turnin<id>`. Returns the full merge sha or ""."""
    tok = _ti_token_re(tid)
    r = repo._git("log", "--merges", "-E",
                  f"--grep=(turnin|bundle)[ _]?{tid}([^0-9]|$)",
                  "--format=%H%x00%s", "-n", "40", timeout=45)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            sha, _, subj = line.partition("\x00")
            if tok.search(subj):
                return sha
    rt = repo._git("tag", "--list", f"*turnin{tid}", timeout=20)
    if rt.returncode == 0:
        for tag in rt.stdout.split():
            if tok.search(tag):
                rp = repo._git("rev-list", "-n", "1", tag, timeout=20)
                if rp.returncode == 0 and rp.stdout.strip():
                    return rp.stdout.strip()
    return ""


def _diff_files(repo: RepoInfo, base: str, target: str, timeout: int = 90) -> list[dict]:
    """List files changed between base..target with +/- line counts.

    numstat supplies adds/dels ('-' for binary); name-status supplies the change
    letter. Rename detection is intentionally off so the two line up by path."""
    files: list[dict] = []
    idx: dict[str, dict] = {}
    r = repo._git("diff", "--numstat", "--no-color", base, target, timeout=timeout)
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], "\t".join(parts[2:])
        rec = {"path": path, "add": 0 if a == "-" else int(a or 0),
               "del": 0 if d == "-" else int(d or 0),
               "binary": a == "-", "status": "M"}
        idx[path] = rec
        files.append(rec)
    r2 = repo._git("diff", "--name-status", "--no-color", base, target, timeout=timeout)
    for line in (r2.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0][:1], parts[-1]
        if path in idx:
            idx[path]["status"] = status
        else:
            rec = {"path": path, "add": 0, "del": 0, "binary": False, "status": status}
            idx[path] = rec
            files.append(rec)
    files.sort(key=lambda f: (-(f["add"] + f["del"]), f["path"]))
    return files


def _files_stat(files: list[dict]) -> dict:
    return {"files": len(files),
            "add": sum(f["add"] for f in files),
            "del": sum(f["del"] for f in files)}


def build_ti(repo: RepoInfo, tid: str, force: bool = False) -> dict:
    """TI lens payload: the introducing merge, the commits it brought in, the
    files it changed, referenced HSDs and contributing authors — the TeamHub
    turnin drill-down for a single turnin, derived from git."""
    ck = _ck(repo, tid)
    if not force:
        cached = _cache_read("ti", ck)
        if cached is not None:
            return cached

    merge = resolve_turnin_merge(repo, tid)
    if not merge:
        return {"error": "turnin not found in this repo's history",
                "id": tid, "repo": repo.key, "head": repo.head}

    meta = repo._git("show", "-s",
                     "--format=%H%x00%an%x00%ae%x00%at%x00%s%x00%b%x00%P", merge)
    msha, m_author, m_mail, m_subj, m_body = merge, "", "", "", ""
    m_time, parents = 0, []
    if meta.returncode == 0 and meta.stdout:
        p = meta.stdout.split("\x00")
        if len(p) >= 7:
            msha, m_author, m_mail, at, m_subj, m_body, par = p[:7]
            m_time = int(at or 0)
            parents = par.split()

    mi = _INCOMING_TI_RE.search(m_subj)
    incoming = mi.group(1) if mi else ""

    commits: list[dict] = []
    hsd_text = f"{m_subj}\n{m_body}"
    if len(parents) >= 2:
        rc = repo._git("log", f"{merge}^1..{merge}^2",
                       "--format=%H%x00%an%x00%at%x00%s%x00%P", "-n", "500")
        for line in (rc.stdout or "").splitlines():
            f = line.split("\x00")
            if len(f) < 5:
                continue
            sha_, an_, at_, subj_, par_ = f[:5]
            commits.append({"sha": sha_, "short": _short(sha_), "author": an_,
                            "time": int(at_ or 0), "summary": subj_,
                            "merge": len(par_.split()) > 1})
            hsd_text += f"\n{subj_}"
    hsds = _extract_hsds(hsd_text)

    base = f"{merge}^1" if parents else EMPTY_TREE
    files = _diff_files(repo, base, merge)

    authors_seen: dict[str, str] = {}
    for c in commits:
        authors_seen.setdefault(c["author"], "")
    authors_seen.setdefault(m_author, m_mail)
    idmap = resolve_identities(list(authors_seen.items()))
    authors = []
    for name, mail in authors_seen.items():
        ident = idmap.get(name, {})
        authors.append({"name": _normalize_name(name), "raw": name,
                        "idsid": ident.get("idsid", ""),
                        "commits": sum(1 for c in commits
                                       if c["author"] == name and not c["merge"])})
    authors.sort(key=lambda a: -a["commits"])
    for c in commits:
        c["idsid"] = idmap.get(c["author"], {}).get("idsid", "")
        c["author"] = _normalize_name(c["author"])

    tag = ""
    rtag = repo._git("tag", "--points-at", merge, timeout=20)
    if rtag.returncode == 0:
        for t in rtag.stdout.split():
            if _ti_token_re(tid).search(t):
                tag = t
                break

    payload = {
        "id": tid, "repo": repo.key, "head": repo.head,
        "incoming": incoming, "tag": tag,
        "status": "In model" if tag else "Merged",
        "merge": {"sha": msha, "short": _short(msha),
                  "author": _normalize_name(m_author),
                  "idsid": idmap.get(m_author, {}).get("idsid", ""),
                  "time": m_time, "summary": m_subj},
        "n_commits": sum(1 for c in commits if not c["merge"]),
        "commits": commits,
        "files": files,
        "stat": _files_stat(files),
        "hsds": hsds,
        "authors": authors,
    }
    # Overlay authoritative Gatekeeper fields when TiDb knows this TI.
    tidb = _tidb_for_repo(repo, auto_refresh=False)
    if tidb is not None:
        tirec = tidb.ti(tid)
        if tirec:
            payload["gk"] = {
                "user": tirec.get("user"),
                "status": tirec.get("status"),
                "stage": tirec.get("stage"),
                "cluster": tirec.get("cluster"),
                "stepping": tirec.get("stepping"),
                "branch": tirec.get("branch"),
                "bundle_id": tirec.get("bundle_id"),
                "bundle_commit": tirec.get("bundle_commit"),
                "bugs": tirec.get("bugs"),
                "ecos": tirec.get("ecos"),
                "comments": (tirec.get("comments") or "").strip(),
                "code_review_url": tirec.get("code_review_url"),
                "code_review_status": tirec.get("code_review_status"),
                "completed_time": tirec.get("completed_time"),
                "completed_time_epoch": tirec.get("completed_time_epoch"),
                "turnin_time": tirec.get("turnin_time"),
                "n_files": len(tirec.get("files_changed") or []),
            }
    _cache_write("ti", ck, payload)
    return payload


def build_commit(repo: RepoInfo, sha: str, force: bool = False) -> dict:
    """Commit lens payload: full metadata + message, the files it changed with
    per-file +/- counts, and the turnin/HSD/merge it maps to."""
    rp = repo._git("rev-parse", "--verify", f"{sha}^{{commit}}", timeout=20)
    if rp.returncode != 0 or not rp.stdout.strip():
        return {"error": "commit not found", "sha": sha, "repo": repo.key,
                "head": repo.head}
    full = rp.stdout.strip()
    ck = _ck(repo, full)
    if not force:
        cached = _cache_read("commitfull", ck)
        if cached is not None:
            return cached

    info = enrich_commit(repo, full)
    rb = repo._git("show", "-s", "--format=%b", full, timeout=30)
    body = rb.stdout.rstrip("\n") if rb.returncode == 0 else ""

    rpar = repo._git("rev-list", "--parents", "-n", "1", full, timeout=20)
    parents = rpar.stdout.split()[1:] if rpar.returncode == 0 and rpar.stdout else []
    base = parents[0] if parents else EMPTY_TREE
    files = _diff_files(repo, base, full)

    idmap = resolve_identities([(info["author"], info.get("mail", ""))])
    payload = {
        "sha": full, "short": _short(full), "repo": repo.key, "head": repo.head,
        "author": _normalize_name(info["author"]), "raw_author": info["author"],
        "idsid": idmap.get(info["author"], {}).get("idsid", ""),
        "mail": info.get("mail", ""), "time": info.get("time", 0),
        "summary": info.get("summary", ""), "body": body,
        "is_merge": len(parents) > 1, "parents": [_short(p) for p in parents],
        "turnins": info.get("turnins", []), "hsds": info.get("hsds", []),
        "merge": info.get("merge", ""), "merge_summary": info.get("merge_summary", ""),
        "files": files, "stat": _files_stat(files),
    }
    _cache_write("commitfull", ck, payload)
    return payload


# --------------------------------------------------------------------------
# File diff for a single commit — powers the "click a file → see its diff"
# view inside the commit and TI lenses.
# --------------------------------------------------------------------------
_DIFF_MAX_BYTES = 512 * 1024   # cap patch text at 512 KB
_DIFF_MAX_LINES = 4000         # …and 4k lines, whichever hits first


def build_diff(repo: RepoInfo, sha: str, rel: str, force: bool = False) -> dict:
    """`git show <sha> -- <path>` for the diff view. For TI merges we show the
    combined merge diff; for regular commits we show the change vs parent."""
    rp = repo._git("rev-parse", "--verify", f"{sha}^{{commit}}", timeout=20)
    if rp.returncode != 0 or not rp.stdout.strip():
        return {"error": "commit not found", "sha": sha, "path": rel,
                "repo": repo.key, "head": repo.head}
    full = rp.stdout.strip()
    ck = _ck(repo, full, rel)
    if not force:
        cached = _cache_read("diff", ck)
        if cached is not None:
            return cached

    # is this a merge? use -m --first-parent so the diff is meaningful
    rpar = repo._git("rev-list", "--parents", "-n", "1", full, timeout=20)
    parents = rpar.stdout.split()[1:] if rpar.returncode == 0 and rpar.stdout else []
    is_merge = len(parents) > 1

    args = ["show", "--no-color", "--format=", "-U8"]
    if is_merge:
        args += ["-m", "--first-parent"]
    args += [full, "--", rel]
    r = repo._git(*args, timeout=90)
    diff = r.stdout or ""
    truncated = False
    if len(diff.encode("utf-8", "replace")) > _DIFF_MAX_BYTES:
        # keep the header + as many hunks as fit
        head, sep, rest = diff.partition("\n@@")
        allowed = _DIFF_MAX_BYTES - len(head.encode("utf-8", "replace"))
        diff = head + (sep + rest.encode("utf-8", "replace")[:max(0, allowed)]
                       .decode("utf-8", "replace") if sep else "")
        truncated = True
    lines = diff.splitlines()
    if len(lines) > _DIFF_MAX_LINES:
        lines = lines[:_DIFF_MAX_LINES]
        truncated = True
        diff = "\n".join(lines) + "\n"

    add, dele, binary = _file_numstat(repo, full, rel)
    payload = {
        "sha": full, "short": _short(full), "path": rel,
        "repo": repo.key, "head": repo.head,
        "is_merge": is_merge, "parents": [_short(p) for p in parents],
        "add": add, "del": dele, "binary": binary,
        "diff": diff, "truncated": truncated, "empty": not diff.strip(),
    }
    _cache_write("diff", ck, payload)
    return payload


# --------------------------------------------------------------------------
# File-scope views — "TI Scope" and "Commit Scope" tabs. These answer the
# whole-file questions ("who / which TI has ever touched this file?") that the
# range-scoped Context tab can't. To keep the two round-trip-consistent, the
# TI list is *derived from* the commit list — never queried separately.
# --------------------------------------------------------------------------
_FILE_LIMIT = 2000  # cap history walk per file


def _file_numstat(repo: RepoInfo, sha: str, rel: str) -> tuple[int, int, bool]:
    """+add / -del the given commit made to the given file. numstat's line is
    `add\\tdel\\tpath`; both are '-' for binary changes."""
    r = repo._git("show", "--no-color", "--numstat", "--format=", sha,
                  "--follow", "--", rel, timeout=45)
    if r.returncode != 0:
        return (0, 0, False)
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d = parts[0], parts[1]
        binary = a == "-"
        return (0 if binary else int(a or 0),
                0 if binary else int(d or 0),
                binary)
    return (0, 0, False)


def list_file_commits(repo: RepoInfo, rel: str, follow: bool = True,
                      include_merges: bool = False) -> list[str]:
    """SHA list of commits that touched `rel`, newest → oldest.

    Uses `--follow` so rename history is picked up (matches user expectations
    and TeamHub). `--no-merges` by default so per-commit line counts are real
    edits, not merge summaries."""
    args = ["log", "--format=%H"]
    if follow:
        args.append("--follow")
    if not include_merges:
        args.append("--no-merges")
    args += [f"-n", str(_FILE_LIMIT), "--", repo.to_git_path(rel)]
    r = repo._git(*args, timeout=60)
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def build_file_commits(repo: RepoInfo, rel: str, follow: bool = True,
                       force: bool = False) -> dict:
    """Commit Scope payload: every commit that touched this file, each with
    the introducing TI(s) via enrich_commit (same code path as everywhere
    else, so per-commit `turnins` is consistent with lens/context views)."""
    git_path = repo.to_git_path(rel)
    shas = list_file_commits(repo, rel, follow=follow)
    commits: list[dict] = []
    authors_seen: dict[str, str] = {}
    if force:
        for sha in shas:
            _cache_path("commit", _ck(repo, sha)).unlink(missing_ok=True)
    # Cheap metadata pass (git show only) in parallel; intro merge attribution
    # is done once batched via resolve_intro_merges().
    def _one(sha: str) -> dict:
        info = dict(enrich_commit(repo, sha, skip_intro=True))
        add, dele, binary = _file_numstat(repo, sha, git_path)
        info["add"] = add
        info["del"] = dele
        info["binary"] = binary
        return info
    max_workers = min(16, max(4, len(shas))) if shas else 1
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        raw = list(ex.map(_one, shas))
    tidb = _tidb_for_repo(repo)
    if tidb is not None and not tidb.error:
        sha2ti = tidb.attribute_shas(shas, git_path)
    else:
        sha2ti = {}
        intro_map = resolve_intro_merges(repo, shas, rel)
        for s in shas:
            im = intro_map.get(s)
            if im and im.get("intro_turnins"):
                sha2ti[s] = im["intro_turnins"][0]
    for sha, info in zip(shas, raw):
        ti_id = sha2ti.get(sha)
        info["orphan"] = False
        if ti_id:
            info["intro_turnins"] = [ti_id]
            if tidb is not None:
                ti_rec = tidb.ti(ti_id)
                if ti_rec:
                    info["merge"] = (ti_rec.get("bundle_commit") or "")[:12]
                    info["merge_summary"] = (
                        (ti_rec.get("comments") or "").strip().split("\n")[0][:120]
                    )
        elif not info["turnins"]:
            info["orphan"] = True
        _cache_write("commit", _ck(repo, sha), info)
        info["turnins"] = effective_turnins(info)
        commits.append(info)
        authors_seen.setdefault(info["author"], info.get("mail", ""))
    idmap = resolve_identities(list(authors_seen.items()))
    for c in commits:
        c["idsid"] = idmap.get(c["author"], {}).get("idsid", "")
        c["author"] = _normalize_name(c["author"])
    return {
        "path": rel, "repo": repo.key, "head": repo.head,
        "follow": follow, "n_commits": len(commits),
        "truncated": len(commits) >= _FILE_LIMIT,
        "commits": commits,
    }


def build_file_tis(repo: RepoInfo, rel: str, follow: bool = True,
                   force: bool = False) -> dict:
    """TI Scope payload: every TI that ever touched this file.

    Derived — not queried — from the commit list, so:
      * every TI shown has at least one commit in Commit Scope mapping to it,
      * every commit's turnins[] appears in this list,
      * no phantom TIs from unrelated merges (round-trip invariant C1/D3/G3).
    """
    cs = build_file_commits(repo, rel, follow=follow, force=force)
    if cs.get("error"):
        return cs
    bucket: dict[str, dict] = {}
    for c in cs["commits"]:
        for tid in c.get("turnins") or []:
            b = bucket.get(tid)
            if b is None:
                b = bucket[tid] = {
                    "id": tid, "merge": c.get("merge", ""),
                    "merge_summary": c.get("merge_summary", ""),
                    "n_commits": 0, "add": 0, "del": 0,
                    "first_time": c.get("time", 0),
                    "last_time": c.get("time", 0),
                    "authors": {},
                    "commits": [],
                    "subjects": [],   # non-merge commit subjects, dedup order
                    "_seen_subjects": set(),
                }
            b["n_commits"] += 1
            b["add"] += int(c.get("add") or 0)
            b["del"] += int(c.get("del") or 0)
            t = int(c.get("time") or 0)
            if t:
                b["first_time"] = min(b["first_time"] or t, t)
                b["last_time"] = max(b["last_time"], t)
            b["authors"][c["author"]] = b["authors"].get(c["author"], 0) + 1
            b["commits"].append(c["short"])
            subj = (c.get("summary") or "").strip()
            key = subj.lower()
            if subj and key not in b["_seen_subjects"]:
                b["_seen_subjects"].add(key)
                b["subjects"].append(subj)
    # Enrich each TI bucket with the true merge time from the merge sha —
    # what the user thinks of as "when this TI shipped" — and build a
    # consolidated summary from the non-merge commits (much more useful than
    # the boilerplate `Merge branch 'master' of …/user_turnin4787` string).
    for tid, b in bucket.items():
        if b["merge"]:
            r = repo._git("show", "-s", "--format=%at%x00%s", b["merge"],
                          timeout=15)
            if r.returncode == 0 and r.stdout:
                at, _, subj = r.stdout.partition("\x00")
                b["merge_time"] = int(at.strip() or 0)
                b["merge_summary"] = b["merge_summary"] or subj.strip()
            else:
                b["merge_time"] = b["last_time"]
        else:
            b["merge_time"] = b["last_time"]
        b["authors"] = [
            {"name": n, "commits": k}
            for n, k in sorted(b["authors"].items(), key=lambda x: -x[1])
        ]
        # a compact one-line rollup: first N subjects joined with " · "; UI can
        # additionally show the full list on hover.
        b["summary"] = " · ".join(b["subjects"][:5]) if b["subjects"] else b.get("merge_summary", "")
        b.pop("_seen_subjects", None)
    tis = sorted(bucket.values(), key=lambda x: -x.get("merge_time", 0))
    return {
        "path": rel, "repo": repo.key, "head": repo.head,
        "follow": follow, "n_tis": len(tis),
        "n_commits": cs["n_commits"], "truncated": cs.get("truncated", False),
        "tis": tis,
    }


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
class _BadBase(ValueError):
    """Raised when a client-supplied ?base= is invalid."""


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeLens/1.0"

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _repo_from(self, q: dict) -> RepoInfo:
        key = (q.get("repo", [None])[0] or default_repo_key())
        r = get_repo(key)
        base_q = q.get("base", [None])[0]
        if base_q is not None and r.ok:
            try:
                r = r.with_base(base_q)
            except ValueError as e:
                raise _BadBase(str(e)) from e
        return r

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        path = u.path

        if path in ("/", "/index.html"):
            html = (HERE / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        if path == "/api/health":
            self._json({"ok": True, "repos": [get_repo(k).as_dict()
                                              for k in _REPO_SPECS]})
            return

        if path == "/api/repos":
            self._json({"default": default_repo_key(),
                        "repos": [get_repo(k).as_dict() for k in _REPO_SPECS]})
            return

        if path == "/api/repos/validate":
            v = validate_model_root(q.get("path", [""])[0])
            self._json(v, 200 if v["ok"] else 400)
            return

        if path == "/api/repos/bases":
            # List candidate base sub-paths inside the repo. When ?path= is
            # given, list its subdirectories (for a walkable picker); otherwise
            # a short curated list (repo toplevel + common code roots).
            key = (q.get("repo", [None])[0] or default_repo_key())
            r = get_repo(key)
            if not r.ok:
                self._json({"error": r.error}, 503); return
            sub = q.get("path", [""])[0].strip("/")
            try:
                sub_dir = (Path(r.root) / sub).resolve()
                sub_dir.relative_to(Path(r.root).resolve())
            except ValueError:
                self._json({"error": "path escapes repo root"}, 400); return
            if not sub_dir.is_dir():
                self._json({"error": "not a directory"}, 404); return
            dirs = []
            try:
                for e in sorted(sub_dir.iterdir(), key=lambda p: p.name.lower()):
                    if e.name in _TREE_SKIP or e.name.startswith("."):
                        continue
                    if e.is_dir():
                        rel = str(e.relative_to(Path(r.root)))
                        dirs.append(rel)
            except OSError as e:
                self._json({"error": str(e)}, 500); return
            self._json({"root": r.root, "current_base": r.base,
                        "parent": sub, "dirs": dirs})
            return

        try:
            repo = self._repo_from(q)
        except _BadBase as e:
            self._json({"error": str(e)}, 400)
            return
        if not repo.ok:
            self._json({"error": f"repo '{repo.key}' unavailable: {repo.error}"},
                       503)
            return

        if path == "/api/tree":
            self._json(list_tree(repo, q.get("path", [""])[0]))
            return
        if path == "/api/tree/search":
            qs = q.get("q", [""])[0]
            try:
                lim = int(q.get("limit", ["200"])[0])
            except ValueError:
                lim = 200
            self._json(search_files(repo, qs, limit=max(1, min(1000, lim))))
            return
        if path == "/api/tidb/status":
            tidb = _tidb_for_repo(repo, auto_refresh=False)
            if tidb is None:
                self._json({"error": "repo has no intel.cluster/stepping — "
                                     "not a MODEL_ROOT?"}, 200)
                return
            self._json(tidb.status())
            return
        if path == "/api/file":
            rel = q.get("path", [""])[0]
            if not rel:
                self._json({"error": "missing path"}, 400)
                return
            self._json(read_file(repo, rel))
            return
        if path == "/api/ti":
            tid = (q.get("id", [""])[0] or "").strip()
            if not tid.isdigit():
                self._json({"error": "bad turnin id"}, 400)
                return
            force = q.get("force", ["0"])[0] in ("1", "true")
            self._json(build_ti(repo, tid, force=force))
            return
        if path == "/api/commit":
            sha = (q.get("sha", [""])[0] or "").strip()
            if not re.match(r"^[0-9a-fA-F]{4,40}$", sha):
                self._json({"error": "bad sha"}, 400)
                return
            force = q.get("force", ["0"])[0] in ("1", "true")
            self._json(build_commit(repo, sha, force=force))
            return
        if path == "/api/diff":
            sha = (q.get("sha", [""])[0] or "").strip()
            rel = (q.get("path", [""])[0] or "").strip()
            if not re.match(r"^[0-9a-fA-F]{4,40}$", sha):
                self._json({"error": "bad sha"}, 400)
                return
            if not rel:
                self._json({"error": "missing path"}, 400)
                return
            force = q.get("force", ["0"])[0] in ("1", "true")
            self._json(build_diff(repo, sha, rel, force=force))
            return
        if path in ("/api/file/tis", "/api/file/commits"):
            rel = q.get("path", [""])[0]
            if not rel:
                self._json({"error": "missing path"}, 400)
                return
            follow = q.get("follow", ["1"])[0] not in ("0", "false")
            force = q.get("force", ["0"])[0] in ("1", "true")
            if path == "/api/file/tis":
                self._json(build_file_tis(repo, rel, follow=follow, force=force))
            else:
                self._json(build_file_commits(repo, rel, follow=follow, force=force))
            return
        if path in ("/api/blame", "/api/context"):
            rel = q.get("path", [""])[0]
            if not rel:
                self._json({"error": "missing path"}, 400)
                return
            try:
                start = int(q.get("start", ["1"])[0])
                end = int(q.get("end", ["200"])[0])
            except ValueError:
                self._json({"error": "start/end must be integers"}, 400)
                return
            force = q.get("force", ["0"])[0] in ("1", "true")
            if path == "/api/blame":
                self._json(blame_range(repo, rel, start, end))
            else:
                self._json(build_context(repo, rel, start, end, force=force))
            return

        self._json({"error": "not found", "path": path}, 404)

    def _read_json_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0 or n > 64 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _custom_key_for(self, label: str) -> str:
        """Pick a unique key like CUSTOM:fit-a0-master, disambiguating on
        collision."""
        base = f"CUSTOM:{label}" if label else "CUSTOM"
        if base not in _REPO_SPECS:
            return base
        for i in range(2, 100):
            k = f"{base}#{i}"
            if k not in _REPO_SPECS:
                return k
        return base  # give up, will overwrite

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        if u.path == "/api/tidb/refresh":
            try:
                repo = self._repo_from(q)
            except _BadBase as e:
                self._json({"error": str(e)}, 400); return
            if not repo.ok:
                self._json({"error": repo.error}, 503); return
            tidb = _tidb_for_repo(repo, auto_refresh=False)
            if tidb is None:
                self._json({"error": "repo not a MODEL_ROOT"}, 400); return
            tidb.refresh()
            self._json(tidb.status())
            return
        if u.path == "/api/repos/custom":
            body = self._read_json_body()
            raw = (body.get("path") or "").strip()
            v = validate_model_root(raw)
            if not v["ok"]:
                self._json({"ok": False, "error": v["error"], **v}, 400)
                return
            saved = _custom_load()
            # If this exact toplevel is already registered, reuse its key.
            existing = next((k for k, p in saved.items()
                             if os.path.realpath(p) == os.path.realpath(v["root"])),
                            None)
            key = existing or self._custom_key_for(v["label"])
            saved[key] = v["root"]
            _custom_save(saved)
            _register_custom(key, v["root"])
            info = get_repo(key)
            self._json({"ok": True, "key": key, "repo": info.as_dict(),
                        "cluster": v["cluster"], "stepping": v["stepping"],
                        "branch": v["branch"], "label": v["label"]})
            return
        self._json({"error": "not found", "path": u.path}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        if u.path == "/api/repos/custom":
            key = (q.get("key", [""])[0] or "").strip()
            if not key or not key.startswith("CUSTOM"):
                self._json({"ok": False, "error": "missing/invalid key"}, 400)
                return
            saved = _custom_load()
            if key in saved:
                saved.pop(key)
                _custom_save(saved)
            _REPO_SPECS.pop(key, None)
            get_repo.cache_clear()
            self._json({"ok": True})
            return
        self._json({"error": "not found", "path": u.path}, 404)


def main() -> None:
    _load_saved_custom_repos()
    print(f"CodeLens serving on http://localhost:{PORT}", flush=True)
    head_tags: dict[str, str] = {}
    for k in _REPO_SPECS:
        r = get_repo(k)
        status = f"{r.root} (base={r.base or '.'} @ {r.head})" if r.ok \
            else f"UNAVAILABLE: {r.error}"
        print(f"  [{k}] {status}", flush=True)
        if r.ok:
            head_tags[k] = _head_tag(r)
    n = _prune_stale_cache(list(_REPO_SPECS.keys()), head_tags)
    if n:
        print(f"cache: pruned {n} stale entries "
              f"(legacy schema or HEAD moved)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
