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

BASE_OVERRIDE = os.environ.get("CODELENS_BASE")  # e.g. "core/fe"
PORT = int(os.environ.get("CODELENS_PORT", "8770"))

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


def _cache_read(kind: str, key: str):
    p = _cache_path(kind, key)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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


def enrich_commit(repo: RepoInfo, sha: str) -> dict:
    ck = f"{repo.key}_{sha}"
    cached = _cache_read("commit", ck)
    if cached is not None:
        return cached
    info: dict = {"sha": sha, "short": sha[:12], "author": "", "mail": "",
                  "time": 0, "summary": "", "turnins": [], "hsds": []}
    meta = subprocess.run(
        ["git", "-C", repo.root, "--no-pager", "show", "-s",
         "--format=%an%x00%ae%x00%at%x00%s%x00%b", sha],
        capture_output=True, text=True, timeout=30, check=False)
    if meta.returncode == 0 and meta.stdout:
        parts = meta.stdout.split("\x00")
        if len(parts) >= 5:
            info["author"] = parts[0]
            info["mail"] = parts[1].strip("<>")
            info["time"] = int(parts[2] or 0)
            info["summary"] = parts[3]
            body = parts[4]
            msg = f"{parts[3]}\n{body}"
            info["hsds"] = _extract_hsds(msg)
            for m in _TURNIN_RE.finditer(msg):
                t = m.group(1)
                if t not in info["turnins"]:
                    info["turnins"].append(t)
    # Introducing merge → the OLDEST merge on the ancestry path from this
    # commit up to HEAD: the gatekeeper merge that first brought the commit
    # into the model (user_turnin<N> / integrate_bundle<N> / ...).
    #
    # BEWARE: `git log -n1 --reverse` is a footgun — the count is applied
    # *before* the reverse, so it yields the NEWEST merge on the path (≈ HEAD,
    # i.e. the latest turnin) instead of the oldest. That made every line in
    # every file resolve to the most recent turnin. Enumerate the ancestry-path
    # merges and take the first after reversing (the true introducing merge).
    mrg = subprocess.run(
        ["git", "-C", repo.root, "--no-pager", "log", "--merges",
         "--ancestry-path", "--reverse",
         "--format=%H%x00%s", f"{sha}..HEAD"],
        capture_output=True, text=True, timeout=60, check=False)
    if mrg.returncode == 0 and mrg.stdout:
        first = next((ln for ln in mrg.stdout.splitlines() if ln), "")
        if first:
            msha, _, msubj = first.partition("\x00")
            info["merge"] = msha[:12]
            info["merge_summary"] = msubj
            for m in _TURNIN_RE.finditer(msubj):
                t = m.group(1)
                if t not in info["turnins"]:
                    info["turnins"].append(t)
            for h in _extract_hsds(msubj):
                if h not in info["hsds"]:
                    info["hsds"].append(h)
    _cache_write("commit", ck, info)
    return info


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
    for sha in order:
        if force:
            _cache_path("commit", f"{repo.key}_{sha}").unlink(missing_ok=True)
        info = enrich_commit(repo, sha)
        info = dict(info)
        info["lines"] = line_by_sha[sha]
        commits.append(info)
        authors_seen.setdefault(info["author"], info.get("mail", ""))
        for t in info.get("turnins", []):
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
    ck = f"{repo.key}_{tid}"
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
    ck = f"{repo.key}_{full}"
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
# HTTP server
# --------------------------------------------------------------------------
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
        return get_repo(key)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
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

        repo = self._repo_from(q)
        if not repo.ok:
            self._json({"error": f"repo '{repo.key}' unavailable: {repo.error}"},
                       503)
            return

        if path == "/api/tree":
            self._json(list_tree(repo, q.get("path", [""])[0]))
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


def main() -> None:
    print(f"CodeLens serving on http://localhost:{PORT}", flush=True)
    for k in _REPO_SPECS:
        r = get_repo(k)
        status = f"{r.root} (base={r.base or '.'} @ {r.head})" if r.ok \
            else f"UNAVAILABLE: {r.error}"
        print(f"  [{k}] {status}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
