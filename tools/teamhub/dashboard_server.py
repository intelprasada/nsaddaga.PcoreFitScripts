#!/usr/bin/env python3
"""
Live team-performance dashboard server for GFC + JNC (H1 2026).

Recomputes metrics from the live git repos on every /api/data request so a
browser refresh yields real-time data. Serves the single-page dashboard at /.

Env vars (all optional):
  GFC_REPO   override GFC repo path
             (default: newest /nfs/site/proj/gfc/gfc.models.*/core/core-gfc-b0-master-*,
              matching what `setGFC` resolves to)
  JNC_REPO   override JNC repo path
             (default: /p/hdk/rtl/proj_data/xhdk74/bak_latest_turnins/jnc/fit/fit-jnc-a0-master-latest,
              a stable symlink kept pointing at the newest jnc-a0 fit turnin)
  DASH_PORT  port to bind                           (default: 8765)
  SINCE      git --since date                       (default: 2026-01-01)
  UNTIL      git --until date                       (default: 2026-06-30)
  TEAM       comma-separated author names override  (default: built-in roster)
"""
from __future__ import annotations

import json
import os
import re
import collections
import glob
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent

def _latest_model(glob_pat: str) -> str | None:
    """Return the newest model bundle path matching a glob, using the trailing
    workweek tag (e.g. '26ww28b') as the sort key so lexicographic order picks
    the newest turnin. Mirrors what `setGFC` / `setJNCfit` resolve to. Skips
    bundles whose name ends in '-defective' and any without a .git dir."""
    import glob as _glob
    tag_re = re.compile(r"(\d+ww\d+[a-z]?)(?:\.\d+)?$")
    best: tuple[str, str] | None = None
    for p in _glob.glob(glob_pat):
        name = Path(p).name
        if name.endswith("-defective") or "defective" in name:
            continue
        if not (Path(p) / ".git").exists():
            continue
        m = tag_re.search(name)
        key = m.group(1) if m else name
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


# GFC uses the stable "-latest" symlink where available (mirror of setGFC's
# newest turnin); falls back to auto-discovery of the newest non-defective
# core-gfc-b0 model bundle. JNC uses the analogous "-latest" symlink.
_GFC_LATEST = "/p/hdk/rtl/proj_data/xhdk74/bak_latest_turnins/gfc/core/core-gfc-b0-master-latest"
_GFC_DEFAULT = (_GFC_LATEST if (Path(_GFC_LATEST) / ".git").exists()
                else _latest_model("/nfs/site/proj/gfc/gfc.models.*/core/core-gfc-b0-master-*"))
_JNC_DEFAULT = "/p/hdk/rtl/proj_data/xhdk74/bak_latest_turnins/jnc/fit/fit-jnc-a0-master-latest"

REPOS = {
    "GFC": os.environ.get("GFC_REPO", _GFC_DEFAULT or ""),
    "JNC": os.environ.get("JNC_REPO", _JNC_DEFAULT),
}
# Gatekeeper incoming areas hold the raw user_turnin bare repos (bundles the
# engineer pushed) even for in-flight / rejected / cancelled turnins.  We fall
# back to these when a sha is not reachable from the baseline model repo so
# the dashboard can still show the diff.
INCOMING_GLOBS = {
    "GFC": "/nfs/site/proj/gfc/*.basedir.*/incoming/*/user_turnin{tid}",
    "JNC": "/nfs/site/proj/jnc/*.basedir.*/incoming/*/user_turnin{tid}",
}


def _find_bundle_repo(project: str, turnin_id) -> str:
    """Locate the bare bundle repo for a given turnin id, or "" if not found."""
    if not turnin_id or project not in INCOMING_GLOBS:
        return ""
    try:
        tid = int(str(turnin_id).strip())
    except (TypeError, ValueError):
        return ""
    hits = glob.glob(INCOMING_GLOBS[project].format(tid=tid))
    for h in hits:
        # bare repo — look for the tell-tale files
        if os.path.isdir(os.path.join(h, "objects")) and os.path.isfile(os.path.join(h, "HEAD")):
            return h
    return ""


# Path to the HSD bugs list inside the GFC and JNC repos. Each line is a
# single numeric HSD id. Only turnins whose files_changed list this exact
# path get scanned for added HSDs.
HSD_BUGS_PATH = "core/common/cfg/bugs"
_HSD_LINE = re.compile(r"^\+(\d{8,14})\s*$")


def _extract_added_hsds(project: str, shas: list[str], turnin_id) -> list[str]:
    """Return the HSD ids added to core/common/cfg/bugs by this turnin.

    Only lines added (starting with `+`) that are pure numeric HSD ids
    are collected — this ignores deletions and unrelated context lines.
    Tries the baseline model repo first, then the bundle repo (for
    in-flight turnins) if none of the shas resolve there.
    """
    if project not in REPOS:
        return []
    shas = [s for s in (shas or []) if s and re.match(r"^[0-9a-fA-F]{7,40}$", s)]
    if not shas and not turnin_id:
        return []

    def _run(base_args: list[str], sha: str) -> str:
        # `-m --first-parent` handles merge commits (bundle_commit is a merge).
        r = subprocess.run(
            base_args + ["--no-pager", "show", "--no-color", "-m", "--first-parent",
                         "--pretty=format:", sha, "--", HSD_BUGS_PATH],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return r.stdout or ""

    def _exists(base_args: list[str], sha: str) -> bool:
        r = subprocess.run(base_args + ["cat-file", "-e", sha + "^{commit}"],
                           capture_output=True, text=True, timeout=10, check=False)
        return r.returncode == 0

    def _parse(diff: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for line in diff.splitlines():
            if line.startswith("+++"):
                continue
            m = _HSD_LINE.match(line)
            if m:
                hsd = m.group(1)
                if hsd not in seen:
                    seen.add(hsd); found.append(hsd)
        return found

    # 1) baseline repo
    baseline = ["git", "-C", REPOS[project]]
    for s in shas:
        if _exists(baseline, s):
            hsds = _parse(_run(baseline, s))
            if hsds:
                return hsds
            # sha resolved but no HSDs added — no need to try other repos
            return []
    # 2) bundle repo (in-flight turnins)
    bundle_repo = _find_bundle_repo(project, turnin_id)
    if bundle_repo:
        bundle = ["git", f"--git-dir={bundle_repo}"]
        for s in shas:
            if _exists(bundle, s):
                return _parse(_run(bundle, s))
        # last resort: hdk_turnin<N> tag inside the bundle repo
        tag = f"hdk_turnin{turnin_id}"
        r = subprocess.run(bundle + ["rev-parse", "--verify", tag + "^{commit}"],
                           capture_output=True, text=True, timeout=10, check=False)
        if r.returncode == 0:
            return _parse(_run(bundle, r.stdout.strip()))
    return []


SINCE = os.environ.get("SINCE", "2026-01-01")
UNTIL = os.environ.get("UNTIL", "2026-06-30")
PORT = int(os.environ.get("DASH_PORT", "8765"))
DEFAULT_RANGE = os.environ.get("RANGE", "H1")  # H1|H2|YTD|MTD|FY|CUSTOM

# --- Persistent on-disk cache ---------------------------------------------
# Rationale: past data (settled turnins, historical commits) doesn't change,
# so we persist mined results across process restarts. Windows whose `until`
# is strictly in the past are treated as immutable; live windows (until >=
# today) respect the *_TTL values below. All caches can be bypassed via a
# `force=1` query param on any API endpoint.
CACHE_DIR = HERE / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
# Cache freshness. Past windows (whose `until` is strictly before today) are
# treated as immutable and served from disk forever. Only *live* windows —
# YTD, MTD, or the current H1/H2 while it's still in progress — respect
# these TTLs. Since users have an explicit ⟳ Force refetch button that
# bypasses all caches, we default to a long day-scale TTL so routine
# refreshes stay instant and only occasionally re-hit git/turnininfo.
TURNIN_TTL       = int(os.environ.get("TURNIN_TTL",       "86400"))  # 24h
GIT_REPORT_TTL   = int(os.environ.get("GIT_REPORT_TTL",   "86400"))  # 24h
TEAM_TURNINS_TTL = int(os.environ.get("TEAM_TURNINS_TTL", "86400"))  # 24h


def _cache_path(kind: str, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    return CACHE_DIR / f"{kind}__{safe}.json"


def _cache_read(kind: str, key: str) -> dict | None:
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
        tmp.write_text(json.dumps({"ts": time.time(), "payload": payload}),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _window_is_past(window: dict) -> bool:
    """True if the whole window ends strictly before today."""
    try:
        return window.get("until", "") < datetime.now().date().isoformat()
    except Exception:
        return False


# Direct reports pulled from phonebook (MgrWWID=11342477) on 2026-07-14,
# plus Niharika and Edwin explicitly included by request.
DEFAULT_TEAM = [
    "Gautham Ajith",
    "Kushwanth Bandanadham",
    "Sachin Bhattad",
    "Kelsey Byers",
    "Namratha Jammalamadugu",
    "Muana Kasongo",
    "Yongxi Li",
    "Ragavi Nagarathinam",
    "Aboli Sawant",
    "Akash Kumar Vruddhula",
    "Niharika Chatla",
    "Edwin Mendez Valverde",
]
TEAM = [n.strip() for n in os.environ.get("TEAM", ",".join(DEFAULT_TEAM)).split(",") if n.strip()]

# --- Phonebook IDSID/WWID resolution ---------------------------------------
# Fallback map for names whose phonebook BookName differs from the display name
# (or that resolve ambiguously). Values checked before phonebook is called.
IDSID_HINTS: dict[str, tuple[str, str]] = {
    # "Display Name": ("idsid", "wwid")
    "Kushwanth Bandanadham":  ("gbandana", "12308499"),  # BookName: Bandanadham, Gnanamaria Kushwanth
    "Yongxi Li":              ("yongxili", "12175166"),  # short surname 'Li' matches wrong person
    "Edwin Mendez Valverde":  ("efmendez", "10656825"),  # disambiguate from Jose Manuel Mendez Valverde
}

_IDSID_CACHE_FILE = HERE / ".idsid_cache.json" if False else None  # inline below
# Use a lazy file-scoped cache path so we can override in tests if needed.
IDSID_CACHE_PATH = Path(os.environ.get("IDSID_CACHE", str(HERE / ".idsid_cache.json")))


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


def _phonebook_lookup(display_name: str) -> tuple[str, str] | None:
    """Look up (idsid, wwid) for a "First [Middle] Last" display name via the
    Intel phonebook CLI. Phonebook stores names as "Last, First [Middle]"; we
    query by the last-name token and then match a row whose BookName also
    contains the first name (case-insensitively). Returns None if unresolved."""
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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


def resolve_identities(names: list[str]) -> dict[str, dict]:
    """Return {display_name: {idsid, wwid}} for every name, using an on-disk
    cache. Missing entries are filled from IDSID_HINTS then phonebook."""
    cache = _load_idsid_cache()
    changed = False
    for n in names:
        if cache.get(n, {}).get("idsid"):
            continue
        if n in IDSID_HINTS:
            idsid, wwid = IDSID_HINTS[n]
            cache[n] = {"idsid": idsid, "wwid": wwid, "source": "hint"}
            changed = True
            continue
        pb = _phonebook_lookup(n)
        if pb:
            cache[n] = {"idsid": pb[0], "wwid": pb[1], "source": "phonebook"}
        else:
            cache[n] = {"idsid": "", "wwid": "", "source": "unresolved"}
        changed = True
    if changed:
        _save_idsid_cache(cache)
    return {n: cache.get(n, {"idsid": "", "wwid": ""}) for n in names}


# --- Gatekeeper turnininfo integration -------------------------------------
# turnininfo needs the HDK environment sourced, which takes ~15s. Results
# are cached per (project, idsid) — TTL controlled by TURNIN_TTL above.

PROJECT_HDK = {
    "GFC": ["-cfg", "xhdk74_sles15", "-model_shell", "-org", "cdg",
            "-m", "core", "-s", "gfc-b0", "-b", "master"],
    "JNC": ["-cfg", "xhdk74_sles15", "-model_shell", "-org", "cdg",
            "-m", "fit", "-s", "jnc-a0", "-b", "master"],
}
_TURNIN_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def _tcsh_hdk_run(project: str, cmd: str, timeout: int = 180) -> str:
    """Run a shell command inside a sourced HDK env for the given project."""
    if project not in PROJECT_HDK:
        return ""
    args = " ".join(PROJECT_HDK[project])
    script = f"source /p/hdk/rtl/hdk.rc {args} > /dev/null; {cmd}"
    try:
        r = subprocess.run(["tcsh", "-c", script],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _extract_json(text: str) -> list | dict | None:
    """turnininfo's JSON output is preceded by ~20+ lines of setup chatter.
    Grab the substring starting at the first '[' or '{' and parse."""
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        i = text.find(open_ch)
        if i < 0:
            continue
        j = text.rfind(close_ch)
        if j <= i:
            continue
        try:
            return json.loads(text[i:j+1])
        except json.JSONDecodeError:
            continue
    return None


_TURNIN_COMMIT_HDR = re.compile(r"^commit ([0-9a-f]{7,40})", re.MULTILINE)


def _parse_turnin_commits(notes: str) -> list[dict]:
    """Parse the appended `git log` block in turnin_notes into commit records."""
    if not notes:
        return []
    out: list[dict] = []
    blocks = re.split(r"\n(?=commit [0-9a-f]{7,40})", notes)
    for b in blocks:
        m = _TURNIN_COMMIT_HDR.match(b)
        if not m:
            continue
        sha = m.group(1)
        lines = b.splitlines()
        is_merge = any(ln.startswith("Merge: ") for ln in lines[:3])
        # Subject = first indented line after the blank separator
        subj = ""
        try:
            blank = lines.index("")
            for ln in lines[blank+1:]:
                if ln.strip():
                    subj = ln.strip()
                    break
        except ValueError:
            pass
        author = ""
        date = ""
        for ln in lines[:6]:
            if ln.startswith("Author: "):
                author = ln[len("Author: "):].strip()
            elif ln.startswith("Date:   "):
                date = ln[len("Date:   "):].strip()
        out.append({"sha": sha, "merge": is_merge, "subject": subj,
                    "author": author, "date": date})
    return out


def mine_turnins(project: str, idsid: str, window: dict, force: bool = False) -> list[dict]:
    """Return the engineer's turnins for the given project inside `window`.
    Cached in-memory per (project, idsid) with TURNIN_TTL and mirrored to
    disk so results survive process restarts. `force=True` bypasses both
    caches and refetches from turnininfo."""
    if not idsid or project not in PROJECT_HDK:
        return []
    key = (project, idsid)
    disk_key = f"{project}_{idsid}"
    now = time.time()
    raw: list[dict] | None = None
    if not force:
        entry = _TURNIN_CACHE.get(key)
        if entry and (now - entry[0]) < TURNIN_TTL:
            raw = entry[1]
        else:
            disk = _cache_read("turnins_raw", disk_key)
            if disk and (now - disk.get("ts", 0)) < TURNIN_TTL:
                raw = disk.get("payload") or []
                _TURNIN_CACHE[key] = (disk["ts"], raw)
    if raw is None:
        # Query a wide-enough window; -days N is the only date filter turnininfo has.
        try:
            since_dt = datetime.strptime(window["since"], "%Y-%m-%d").date()
        except ValueError:
            since_dt = datetime.now().date()
        days = max(1, (datetime.now().date() - since_dt).days + 30)
        cmd = f"turnininfo -user {idsid} -days {days} -all -format json"
        out = _tcsh_hdk_run(project, cmd, timeout=180)
        parsed = _extract_json(out)
        raw = parsed if isinstance(parsed, list) else []
        _TURNIN_CACHE[key] = (now, raw)
        _cache_write("turnins_raw", disk_key, raw)

    since, until = window["since"], window["until"]
    filtered: list[dict] = []
    for t in raw:
        ttime = t.get("turnin_time") or t.get("completed_time") or ""
        if not ttime:
            continue
        d = ttime[:10]
        if d < since or d > until:
            continue
        commits = _parse_turnin_commits(t.get("turnin_notes") or "")
        files_changed = t.get("files_changed") or []
        hsds_added: list[str] = []
        # Use git diff on core/common/cfg/bugs — more accurate than the BUGS:
        # field (catches exact lines added, handles multi-SHA turnins correctly).
        if any((f or "").lower().endswith(HSD_BUGS_PATH) for f in files_changed):
            # bundle_commit is the merge commit in the model — --first-parent gives
            # exactly what this TI introduced. user_commit is a fallback for
            # in-flight/rejected TIs where bundle_commit isn't in the baseline yet.
            shas = [s for s in [t.get("bundle_commit"), t.get("user_commit")] if s]
            hsds_added = _extract_added_hsds(project, shas, t.get("id"))
        # Pipe timing: how long the TI sat in the pipeline. For released/
        # settled TIs use completed - turnin; for still-open TIs we mark it
        # as in-flight so the frontend can show "current age" instead.
        s_ep = t.get("turnin_time_epoch")
        e_ep = t.get("completed_time_epoch")
        pipe_seconds = None
        in_flight = e_ep is None
        if s_ep and e_ep and e_ep > s_ep:
            pipe_seconds = int(e_ep - s_ep)
        filtered.append({
            "id":                t.get("id"),
            "bundle_id":         t.get("bundle_id"),
            "status":            t.get("status"),
            "stage":             t.get("stage"),
            "cluster":           t.get("cluster"),
            "stepping":          t.get("stepping"),
            "branch":            t.get("branch"),
            "model":             t.get("model"),
            "turnin_time":       ttime,
            "completed_time":    t.get("completed_time"),
            "turnin_time_epoch": s_ep,
            "completed_time_epoch": e_ep,
            "pipe_seconds":      pipe_seconds,
            "in_flight":         in_flight,
            "comments":          (t.get("comments") or "").strip(),
            "files_changed":     files_changed,
            "code_review_url":   t.get("code_review_url"),
            "code_review_status": t.get("code_review_status"),
            "user_commit":       t.get("user_commit"),
            "bundle_commit":     t.get("bundle_commit"),
            "bugs":              t.get("bugs") or [],
            "ecos":              t.get("ecos") or [],
            "commits":           commits,
            "n_commits":         sum(1 for c in commits if not c["merge"]),
            "hsds_added":        hsds_added,
            "project":           project,
        })
    filtered.sort(key=lambda x: x["turnin_time"], reverse=True)
    return filtered


def _pipe_stats(seconds_list: list[int]) -> dict:
    """Compute avg/p50/p90/max pipe-time stats from a list of seconds."""
    xs = sorted(int(s) for s in (seconds_list or []) if s and s > 0)
    if not xs:
        return {"n": 0, "avg": None, "p50": None, "p90": None, "max": None}
    n = len(xs)
    avg = sum(xs) // n
    p50 = xs[n // 2]
    # inclusive p90: last element when n<10
    p90 = xs[min(n - 1, max(0, int(round(0.9 * (n - 1)))))]
    return {"n": n, "avg": avg, "p50": p50, "p90": p90, "max": xs[-1]}


def build_turnin_report(engineer: str, project: str, window: dict, identities: dict,
                        force: bool = False) -> dict:
    ident = identities.get(engineer) or {}
    idsid = ident.get("idsid", "")
    projects = [project] if project in PROJECT_HDK else list(PROJECT_HDK.keys())
    all_ti: list[dict] = []
    per_project: dict[str, list[dict]] = {}
    for p in projects:
        ti = mine_turnins(p, idsid, window, force=force)
        per_project[p] = ti
        all_ti.extend(ti)
    all_ti.sort(key=lambda x: x["turnin_time"], reverse=True)
    # Per-engineer pipe-time stats (released → time-to-release; in-flight → current age)
    now_ep = int(time.time())
    released_secs = [t["pipe_seconds"] for t in all_ti
                     if t.get("pipe_seconds") and re.search(r"releas", str(t.get("status") or ""), re.I)]
    inflight_ages = [now_ep - int(t["turnin_time_epoch"]) for t in all_ti
                     if t.get("in_flight") and t.get("turnin_time_epoch")]
    pipe = {
        "released": _pipe_stats(released_secs),
        "in_flight": _pipe_stats(inflight_ages),
    }
    return {
        "engineer": engineer,
        "idsid": idsid,
        "project": project,
        "window": {"since": window["since"], "until": window["until"],
                   "label": window["label"], "range": window["range"], "year": window["year"]},
        "totals": {p: len(v) for p, v in per_project.items()},
        "pipe": pipe,
        "turnins": all_ti,
    }


# ------------------------ Engineer summary generation -----------------------
# Bucket TI comments and file paths into RTL-unit + activity buckets so we
# can produce a factual per-engineer summary card.

# RTL-unit tags. Order matters — more-specific units first. Each entry is
# (label, path_regex, comment_regex).
_UNIT_RULES: list[tuple[str, "re.Pattern", "re.Pattern"]] = [
    ("STSR",      re.compile(r"(^|/)stsr(/|_|\.)|fv_stsr", re.I), re.compile(r"\bstsr\b", re.I)),
    ("IDQ",       re.compile(r"(^|/)idq(/|_|\.)|idqctln|idq\.icf", re.I), re.compile(r"\bidq\b", re.I)),
    ("DSB/DSBE",  re.compile(r"(^|/)dsbe?(/|_|\.)|fv_.*_dsbe?_", re.I), re.compile(r"\bdsbe?\b", re.I)),
    ("MS/MSID",   re.compile(r"(^|/)ms(id)?(/|_|\.)", re.I), re.compile(r"\bms(id)?\b|\bmicrocode sequencer\b", re.I)),
    ("IFU",       re.compile(r"(^|/)ifu(/|_|\.)", re.I), re.compile(r"\bifu\b", re.I)),
    ("BPU",       re.compile(r"(^|/)bpu(/|_|\.)", re.I), re.compile(r"\bbpu\b", re.I)),
    ("BAC",       re.compile(r"(^|/)bac(/|_|\.)", re.I), re.compile(r"\bbac\b", re.I)),
    ("IDU",       re.compile(r"(^|/)idu(/|_|\.)", re.I), re.compile(r"\bidu\b", re.I)),
    ("IQ",        re.compile(r"(^|/)iq(/|_|\.)|fv_.*_iq_", re.I), re.compile(r"\biq\b", re.I)),
    ("RAT",       re.compile(r"(^|/)rat(/|_|\.)", re.I), re.compile(r"\brat\b", re.I)),
    ("MCA",       re.compile(r"(^|/)mca(/|_|\.)", re.I), re.compile(r"\bmca\b", re.I)),
    ("FV/FPV infra", re.compile(r"(^|/)(fpv|formal)(/|_|\.)", re.I), re.compile(r"\bfpv\b|\bformal\b", re.I)),
    ("CTE",       re.compile(r"(^|/)cte(/|_|\.)", re.I), re.compile(r"\bcte\b", re.I)),
]

# Activity buckets. First match wins (per TI).
_ACTIVITY_RULES: list[tuple[str, "re.Pattern"]] = [
    ("Coverage",           re.compile(r"\b(cover|coverage|unconverged|val ?plan)\b", re.I)),
    ("Emulation debug",    re.compile(r"\b(emu|emulation)\b", re.I)),
    ("Assertion tuning",   re.compile(r"\b(assume|assert(ion)?|disable[d]?.*(assert|assume))\b", re.I)),
    ("Bug fix / debug",    re.compile(r"\b(fix|failure|escape|root ?cause|bug ?id|hsd)\b", re.I)),
    ("Feature enablement", re.compile(r"\b(enable|enablement|adding|added|implement|deliver|introduce|MT0|SMT|LSD.?stall|xlat|parities|parity|LFSR)\b", re.I)),
    ("Cleanup / codeowner", re.compile(r"\b(cleanup|clean up|codeowner|format|rename|typo|comment)\b", re.I)),
]


def _classify_ti(t: dict) -> dict:
    """Return {units:[..], activity:'..' or None} for a single TI."""
    text = (t.get("comments") or "")
    for c in (t.get("commits") or []):
        text += "\n" + (c.get("subject") or "")
    files = t.get("files_changed") or []
    units: list[str] = []
    for label, path_re, cmt_re in _UNIT_RULES:
        if any(path_re.search(f or "") for f in files) or cmt_re.search(text):
            units.append(label)
    activity = None
    for label, pat in _ACTIVITY_RULES:
        if pat.search(text):
            activity = label
            break
    return {"units": units, "activity": activity}


def build_engineer_summary(engineer: str, ti_list: list[dict], projects: list[str]) -> dict:
    """Aggregate a per-engineer factual summary from a list of enriched TIs."""
    total = len(ti_list)
    by_status: dict[str, int] = {}
    by_project: dict[str, int] = {p: 0 for p in projects}
    hsds: set[str] = set()
    releasing_hsd_tis = 0
    file_counts: collections.Counter = collections.Counter()
    unit_tis: dict[str, list] = {}      # unit -> list[(ti_id, project, activity)]
    activity_by_unit: dict[str, collections.Counter] = {}
    orphan_tis: list = []               # TIs that didn't map to any unit
    for t in ti_list:
        st = (t.get("status") or "unknown").lower()
        by_status[st] = by_status.get(st, 0) + 1
        pr = t.get("project") or "?"
        by_project[pr] = by_project.get(pr, 0) + 1
        added = t.get("hsds_added") or []
        if added:
            releasing_hsd_tis += 1
            for h in added:
                hsds.add(str(h))
        for f in (t.get("files_changed") or []):
            file_counts[f] += 1
        cls = _classify_ti(t)
        entry = (t.get("id"), pr, cls["activity"], (t.get("comments") or "").strip().split("\n")[0][:120])
        if cls["units"]:
            for u in cls["units"]:
                unit_tis.setdefault(u, []).append(entry)
                if cls["activity"]:
                    activity_by_unit.setdefault(u, collections.Counter())[cls["activity"]] += 1
        else:
            orphan_tis.append(entry)
    # Rank units by number of TIs touching them.
    units_ranked = sorted(unit_tis.items(), key=lambda kv: -len(kv[1]))
    unit_sections = []
    for u, entries in units_ranked:
        act_counts = activity_by_unit.get(u, collections.Counter())
        unit_sections.append({
            "unit": u,
            "ti_count": len(entries),
            "activities": [{"name": name, "count": n}
                           for name, n in act_counts.most_common()],
            "ti_ids": [e[0] for e in sorted(entries, key=lambda e: str(e[0]))],
        })
    # Notable / orphan turnins (didn't classify into any unit) — still surface as a bucket.
    if orphan_tis:
        unit_sections.append({
            "unit": "Other",
            "ti_count": len(orphan_tis),
            "activities": [],
            "ti_ids": [e[0] for e in orphan_tis],
        })
    top_files = [{"path": f, "count": c} for f, c in file_counts.most_common(10)]
    return {
        "engineer": engineer,
        "totals": {
            "turnins": total,
            "released": by_status.get("released", 0),
            "cancelled": by_status.get("cancelled", 0) + by_status.get("withdrawn", 0),
            "in_flight": sum(1 for t in ti_list if t.get("in_flight")),
            "by_project": {p: n for p, n in by_project.items() if p in projects},
            "hsds_filed": len(hsds),
            "hsd_tis": releasing_hsd_tis,
        },
        "unit_sections": unit_sections,
        "top_files": top_files,
    }


def build_all_engineer_summaries(project: str, window: dict, identities: dict,
                                  force: bool = False) -> dict:
    """Mine every team member's turnins (parallel-primed) and produce a
    structured summary card for each. Cached to disk under a summaries__ key
    so re-visits are instant across process restarts."""
    disk_key = f"{project}_{window['since']}_{window['until']}"
    if not force:
        cached = _cache_read("summaries", disk_key)
        if cached:
            age = time.time() - cached.get("ts", 0)
            if _window_is_past(window) or age < TEAM_TURNINS_TTL:
                return cached["payload"]
    projects = [project] if project in PROJECT_HDK else list(PROJECT_HDK.keys())
    # Parallel-prime turnininfo per (engineer, project) — reuses on-disk cache.
    jobs: list[tuple[str, str, str]] = []
    for eng in TEAM:
        idsid = (identities.get(eng) or {}).get("idsid", "")
        if not idsid:
            continue
        for p in projects:
            jobs.append((eng, idsid, p))
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as ex:
        futs = {ex.submit(mine_turnins, p, idsid, window, force): (eng, p) for (eng, idsid, p) in jobs}
        results: dict[tuple[str, str], list[dict]] = {}
        for f in futs:
            eng, p = futs[f]
            try:
                results[(eng, p)] = f.result()
            except Exception:
                results[(eng, p)] = []
    summaries = []
    for eng in TEAM:
        idsid = (identities.get(eng) or {}).get("idsid", "")
        ti_list: list[dict] = []
        for p in projects:
            ti_list.extend(results.get((eng, p), []))
        ti_list.sort(key=lambda x: x.get("turnin_time", ""), reverse=True)
        card = build_engineer_summary(eng, ti_list, projects)
        card["idsid"] = idsid
        summaries.append(card)
    result = {
        "project": project,
        "window": {"since": window["since"], "until": window["until"],
                   "label": window["label"], "range": window["range"], "year": window["year"]},
        "engineers": summaries,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache_write("summaries", disk_key, result)
    return result


def build_team_turnin_summary(project: str, window: dict, identities: dict,
                              force: bool = False) -> dict:
    """Aggregate turnin stats across the whole team for the given window.
    Uses cached per-user turnininfo calls; primes the cache in parallel on
    the first hit. Persists the aggregated result to disk so full-team
    Team Overview loads are instant across process restarts."""
    # Fast disk hit for the aggregated result — safe for past windows or
    # while TTL is fresh for live windows.
    disk_key = f"{project}_{window['since']}_{window['until']}"
    if not force:
        cached = _cache_read("team_turnins", disk_key)
        if cached:
            age = time.time() - cached.get("ts", 0)
            if _window_is_past(window) or age < TEAM_TURNINS_TTL:
                return cached["payload"]

    projects = [project] if project in PROJECT_HDK else list(PROJECT_HDK.keys())
    active_months = window["months"]
    # Prime cache in parallel
    jobs: list[tuple[str, str, str]] = []
    for eng in TEAM:
        idsid = (identities.get(eng) or {}).get("idsid", "")
        if not idsid:
            continue
        for p in projects:
            jobs.append((eng, idsid, p))
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as ex:
        futs = {ex.submit(mine_turnins, p, idsid, window, force): (eng, p) for (eng, idsid, p) in jobs}
        results: dict[tuple[str, str], list[dict]] = {}
        for f in futs:
            eng, p = futs[f]
            try:
                results[(eng, p)] = f.result()
            except Exception:
                results[(eng, p)] = []

    per_engineer = []
    team_monthly = {m: 0 for m in active_months}
    team_monthly_released = {m: 0 for m in active_months}
    team_monthly_by_project = {p: {m: 0 for m in active_months} for p in projects}
    team_monthly_released_by_project = {p: {m: 0 for m in active_months} for p in projects}
    status_counts: dict[str, int] = {}
    team_total = 0
    team_files_touched = 0
    team_released_pipe_secs: list[int] = []
    now_ep = int(time.time())
    for eng in TEAM:
        ident = identities.get(eng) or {}
        idsid = ident.get("idsid", "")
        per_proj: dict[str, dict] = {}
        eng_total = 0
        eng_files = 0
        eng_monthly = {m: 0 for m in active_months}
        eng_monthly_released = {m: 0 for m in active_months}
        eng_status: dict[str, int] = {}
        eng_released_secs: list[int] = []
        eng_inflight_ages: list[int] = []
        for p in projects:
            ti_list = results.get((eng, p), [])
            released_list = [t for t in ti_list if re.search(r"releas", str(t.get("status") or ""), re.I)]
            per_proj[p] = {"total": len(ti_list),
                          "released": len(released_list),
                          "files": sum(len(t.get("files_changed") or []) for t in ti_list),
                          "released_files": sum(len(t.get("files_changed") or []) for t in released_list)}
            eng_total += len(ti_list)
            eng_files += per_proj[p]["files"]
            for t in ti_list:
                d = (t.get("turnin_time") or "")[:10]
                try:
                    mnum = datetime.strptime(d, "%Y-%m-%d").month
                except ValueError:
                    continue
                mk = MONTHS[mnum - 1]
                is_released = bool(re.search(r"releas", str(t.get("status") or ""), re.I))
                if mk in eng_monthly:
                    eng_monthly[mk] += 1
                    team_monthly[mk] += 1
                    team_monthly_by_project[p][mk] += 1
                    if is_released:
                        eng_monthly_released[mk] = eng_monthly_released.get(mk, 0) + 1
                        team_monthly_released[mk] = team_monthly_released.get(mk, 0) + 1
                        team_monthly_released_by_project[p][mk] = team_monthly_released_by_project[p].get(mk, 0) + 1
                st = t.get("status") or "unknown"
                eng_status[st] = eng_status.get(st, 0) + 1
                status_counts[st] = status_counts.get(st, 0) + 1
                # Pipe-time collection
                if is_released and t.get("pipe_seconds"):
                    eng_released_secs.append(int(t["pipe_seconds"]))
                    team_released_pipe_secs.append(int(t["pipe_seconds"]))
                elif t.get("in_flight") and t.get("turnin_time_epoch"):
                    eng_inflight_ages.append(now_ep - int(t["turnin_time_epoch"]))
        per_engineer.append({
            "engineer": eng,
            "idsid": idsid,
            "total": eng_total,
            "released": sum(v.get("released", 0) for v in per_proj.values()),
            "files": eng_files,
            "released_files": sum(v.get("released_files", 0) for v in per_proj.values()),
            "per_project": per_proj,
            "monthly": eng_monthly,
            "monthly_released": eng_monthly_released,
            "status": eng_status,
            "pipe_released": _pipe_stats(eng_released_secs),
            "pipe_in_flight": _pipe_stats(eng_inflight_ages),
        })
        team_total += eng_total
        team_files_touched += eng_files

    team_released_total = sum(e["released"] for e in per_engineer)
    result = {
        "project": project,
        "window": {"since": window["since"], "until": window["until"],
                   "label": window["label"], "range": window["range"], "year": window["year"]},
        "months": active_months,
        "team_totals": {"turnins": team_total, "files": team_files_touched,
                        "released": team_released_total,
                        "engineers": sum(1 for e in per_engineer if e["total"] > 0)},
        "team_pipe_released": _pipe_stats(team_released_pipe_secs),
        "team_totals_by_project": {p: sum(e["per_project"].get(p, {}).get("total", 0) for e in per_engineer)
                                   for p in projects},
        "team_totals_released_by_project": {p: sum(e["per_project"].get(p, {}).get("released", 0) for e in per_engineer)
                                            for p in projects},
        "team_monthly": team_monthly,
        "team_monthly_released": team_monthly_released,
        "team_monthly_by_project": team_monthly_by_project,
        "team_monthly_released_by_project": team_monthly_released_by_project,
        "status_counts": status_counts,
        "engineers": per_engineer,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _cache_write("team_turnins", disk_key, result)
    return result


# Categories -> keyword patterns (case-insensitive, matched against subject line)
CATEGORY_PATTERNS = [
    ("Bug Fixes",             re.compile(r"\b(bug|fix|bugtrack|hotfix)\b", re.I)),
    ("Feature Implementations", re.compile(r"\b(feature|add|implement|new|enable|support)\b", re.I)),
    ("CTE Updates",           re.compile(r"\bcte\b", re.I)),
    ("Coverage Improvements", re.compile(r"\b(cov|coverage)\b", re.I)),
    ("Code Quality",          re.compile(r"\b(lint|cleanup|clean up|rename|refactor|comment|typo|format)\b", re.I)),
]
CATEGORIES = [c for c, _ in CATEGORY_PATTERNS] + ["Other"]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def resolve_window(range_key: str, year: int | None = None,
                   since: str | None = None, until: str | None = None) -> dict:
    """Resolve a UI range selection into a concrete git window.

    range_key ∈ {YTD, MTD, H1, H2, FY, CUSTOM}. When CUSTOM, since/until are
    used verbatim (fallback to server SINCE/UNTIL if missing)."""
    today = datetime.now().date()
    y = year or today.year
    rk = (range_key or "H1").upper()
    if rk == "CUSTOM":
        s = since or SINCE
        u = until or UNTIL
        label = f"{s} → {u}"
        try:
            m_from = datetime.strptime(s, "%Y-%m-%d").month
            m_to   = datetime.strptime(u, "%Y-%m-%d").month
            months = MONTHS[m_from-1:m_to]
        except ValueError:
            months = MONTHS
        return {"since": s, "until": u, "months": months, "label": label,
                "range": "CUSTOM", "year": y}
    if rk == "YTD":
        s = f"{y}-01-01"
        u = today.strftime("%Y-%m-%d") if y == today.year else f"{y}-12-31"
        months = MONTHS[:today.month] if y == today.year else MONTHS
        label = f"YTD {y}"
    elif rk == "MTD":
        first = today.replace(day=1) if y == today.year else datetime(y, 1, 1).date()
        s = first.strftime("%Y-%m-%d")
        u = today.strftime("%Y-%m-%d") if y == today.year else f"{y}-01-31"
        months = [MONTHS[first.month - 1]]
        label = f"{months[0]} {y}"
    elif rk == "H1":
        s, u = f"{y}-01-01", f"{y}-06-30"
        months = MONTHS[:6]
        label = f"H1 {y}"
    elif rk == "H2":
        s, u = f"{y}-07-01", f"{y}-12-31"
        months = MONTHS[6:]
        label = f"H2 {y}"
    elif rk == "FY":
        s, u = f"{y}-01-01", f"{y}-12-31"
        months = MONTHS
        label = f"FY {y}"
    else:
        return resolve_window("H1", y)
    return {"since": s, "until": u, "months": months, "label": label,
            "range": rk, "year": y}

# ---------------------------------------------------------------------------
# Git mining
# ---------------------------------------------------------------------------
def _run_git(repo: str, args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", repo, "--no-pager", *args],
            capture_output=True, text=True, timeout=90, check=False,
        )
        return out.stdout
    except Exception as e:  # pragma: no cover
        print(f"[git err] {repo} {' '.join(args)}: {e}")
        return ""


def _author_regex(name: str) -> str:
    """Build a POSIX regex matching a git author for this person, tolerating
    both 'Last, First [Middle]' (Intel default) and 'First Last' variants.
    Uses the first token as first-name and the last token as surname, allowing
    optional middle names in between."""
    parts = [p for p in re.split(r"[\s,]+", name) if p]
    if not parts:
        return re.escape(name)
    if len(parts) == 1:
        return re.escape(parts[0])
    first, last = re.escape(parts[0]), re.escape(parts[-1])
    # "Last, ... First" | "First ... Last"
    return f"({last},[^,]*{first}|{first}[^,]*{last})"


def mine_engineer(repo: str, author: str, window: dict) -> dict:
    """Return metrics for one engineer in one repo for the given window."""
    fmt = "--pretty=format:%x1fCOMMIT%x1f%H%x1f%ad%x1f%s"
    raw = _run_git(
        repo,
        [
            "log",
            f"--since={window['since']}",
            f"--until={window['until']}",
            "--no-merges",
            "--extended-regexp",
            "-i",
            f"--author={_author_regex(author)}",
            "--date=short",
            "--numstat",
            fmt,
        ],
    )

    commits: list[dict] = []
    cur: dict | None = None
    for line in raw.splitlines():
        if line.startswith("\x1fCOMMIT\x1f"):
            if cur is not None:
                commits.append(cur)
            _, _, sha, date, *rest = line.split("\x1f")
            subject = "\x1f".join(rest) if rest else ""
            cur = {"sha": sha, "date": date, "subject": subject,
                   "add": 0, "del": 0, "file_stats": []}
        elif line.strip() and cur is not None:
            m = re.match(r"^(\d+|-)\s+(\d+|-)\s+(.+)$", line)
            if m:
                a = 0 if m.group(1) == "-" else int(m.group(1))
                d = 0 if m.group(2) == "-" else int(m.group(2))
                path = m.group(3).strip()
                if " => " in path:
                    brace = re.search(r"\{([^{}]*) => ([^{}]*)\}", path)
                    if brace:
                        path = path.replace(brace.group(0), brace.group(2)).replace("//", "/")
                    else:
                        path = path.split(" => ")[-1].strip()
                cur["add"] += a
                cur["del"] += d
                cur["file_stats"].append({"path": path, "add": a, "del": d})
    if cur is not None:
        commits.append(cur)

    # Aggregate
    total = len(commits)
    per_commit_lines = [c["add"] + c["del"] for c in commits]
    net_lines = sum(c["add"] - c["del"] for c in commits)
    avg = round(statistics.mean(per_commit_lines), 1) if per_commit_lines else 0.0
    med = round(statistics.median(per_commit_lines), 1) if per_commit_lines else 0.0
    at_or_below = sum(1 for x in per_commit_lines if x <= med) if per_commit_lines else 0
    pct = round(100.0 * at_or_below / total, 1) if total else 0.0

    active_months = window["months"]
    monthly = {m: {"commits": 0, "net": 0} for m in active_months}
    categories = {c: 0 for c in CATEGORIES}
    # path -> {commits, add, del, net, commits_list: [{sha, subject, date, add, del, net}]}
    files: dict[str, dict] = {}
    for c in commits:
        try:
            mnum = datetime.strptime(c["date"], "%Y-%m-%d").month
        except ValueError:
            continue
        key = MONTHS[mnum - 1]
        if key in monthly:
            monthly[key]["commits"] += 1
            monthly[key]["net"] += c["add"] - c["del"]
        subj = c["subject"] or ""
        matched = False
        for cat, pat in CATEGORY_PATTERNS:
            if pat.search(subj):
                categories[cat] += 1
                matched = True
                break
        if not matched:
            categories["Other"] += 1
        for fs in c["file_stats"]:
            path = fs["path"]
            f = files.setdefault(path, {
                "commits": 0, "add": 0, "del": 0, "net": 0, "commits_list": []
            })
            f["commits"] += 1
            f["add"]     += fs["add"]
            f["del"]     += fs["del"]
            f["net"]     += (fs["add"] - fs["del"])
            f["commits_list"].append({
                "sha": c["sha"][:12],
                "subject": subj,
                "date": c["date"],
                "add": fs["add"],
                "del": fs["del"],
                "net": fs["add"] - fs["del"],
            })

    pattern = _work_pattern(avg, med, pct)
    # Compact per-commit list for the UI (drop internal "file_stats" set semantics
    # and keep only what the frontend needs).
    commits_out = [
        {
            "sha":     c["sha"][:12],
            "full_sha": c["sha"],
            "date":    c["date"],
            "subject": c["subject"],
            "add":     c["add"],
            "del":     c["del"],
            "net":     c["add"] - c["del"],
            "files":   len(c["file_stats"]),
            "file_stats": c["file_stats"],
        }
        for c in commits
    ]
    commits_out.sort(key=lambda c: c["date"], reverse=True)
    return {
        "engineer": author,
        "total": total,
        "net_lines": net_lines,
        "avg_lines": avg,
        "median_lines": med,
        "at_or_below": at_or_below,
        "pct_at_or_below": pct,
        "pattern": pattern,
        "monthly": monthly,
        "categories": categories,
        "files": files,
        "commits": commits_out,
    }


def _work_pattern(avg: float, med: float, pct: float) -> str:
    if avg >= 40:
        return "Large feature commits"
    if avg >= 20 and med <= 3:
        return "Mix of small/large"
    if avg >= 20:
        return "Consistent medium size"
    if avg >= 10:
        return "Small consistent work"
    return "Minimal-change updates"


def build_report(project: str, window: dict, force: bool = False) -> dict:
    """project in {GFC, JNC, ALL}; window from resolve_window(). The full
    report is memoized on disk keyed by (project, since, until). Past
    windows are treated as immutable; live windows respect GIT_REPORT_TTL."""
    disk_key = f"{project}_{window['since']}_{window['until']}"
    if not force:
        cached = _cache_read("report", disk_key)
        if cached:
            age = time.time() - cached.get("ts", 0)
            if _window_is_past(window) or age < GIT_REPORT_TTL:
                return cached["payload"]

    targets = [project] if project in REPOS else list(REPOS.keys())
    per_engineer: list[dict] = []
    active_months = window["months"]
    identities = resolve_identities(TEAM)

    for eng in TEAM:
        ident = identities.get(eng, {})
        merged = {
            "engineer": eng,
            "idsid": ident.get("idsid", ""),
            "wwid":  ident.get("wwid", ""),
            "total": 0, "net_lines": 0,
            "avg_lines": 0.0, "median_lines": 0.0,
            "at_or_below": 0, "pct_at_or_below": 0.0,
            "pattern": "",
            "monthly": {m: {"commits": 0, "net": 0} for m in active_months},
            "categories": {c: 0 for c in CATEGORIES},
            "files": {},
            "commits": [],
            "per_project": {},
        }
        all_per_commit: list[int] = []
        for proj in targets:
            repo = REPOS[proj]
            if not (Path(repo) / ".git").exists():
                merged["per_project"][proj] = {"error": f"no .git at {repo}"}
                continue
            r = mine_engineer(repo, eng, window)
            merged["per_project"][proj] = r
            merged["total"] += r["total"]
            merged["net_lines"] += r["net_lines"]
            for m in active_months:
                merged["monthly"][m]["commits"] += r["monthly"][m]["commits"]
                merged["monthly"][m]["net"]     += r["monthly"][m]["net"]
            for c in CATEGORIES:
                merged["categories"][c] += r["categories"][c]
            for path, stats in r["files"].items():
                key = f"[{proj}] {path}" if len(targets) > 1 else path
                cur = merged["files"].setdefault(key, {
                    "commits": 0, "add": 0, "del": 0, "net": 0, "commits_list": [], "project": proj, "path": path,
                })
                cur["commits"] += stats["commits"]
                cur["add"]     += stats["add"]
                cur["del"]     += stats["del"]
                cur["net"]     += stats["net"]
                for cl in stats["commits_list"]:
                    cl2 = dict(cl); cl2["project"] = proj
                    cur["commits_list"].append(cl2)
            for c in r["commits"]:
                c2 = dict(c); c2["project"] = proj
                merged["commits"].append(c2)
            # Re-derive avg/median across combined projects using approximation
            all_per_commit.extend([r["avg_lines"]] * r["total"])
        if all_per_commit:
            merged["avg_lines"] = round(statistics.mean(all_per_commit), 1)
            merged["median_lines"] = round(statistics.median(all_per_commit), 1)
            merged["at_or_below"] = sum(1 for x in all_per_commit if x <= merged["median_lines"])
            merged["pct_at_or_below"] = round(100.0 * merged["at_or_below"] / len(all_per_commit), 1)
            merged["pattern"] = _work_pattern(merged["avg_lines"], merged["median_lines"], merged["pct_at_or_below"])
        # Sort each file's commit list by date desc so the newest touches are on top
        for stats in merged["files"].values():
            stats["commits_list"].sort(key=lambda c: c["date"], reverse=True)
        merged["commits"].sort(key=lambda c: c["date"], reverse=True)
        per_engineer.append(merged)

    # Team totals
    team_totals = {
        "total": sum(e["total"] for e in per_engineer),
        "net_lines": sum(e["net_lines"] for e in per_engineer),
        "engineers": len([e for e in per_engineer if e["total"] > 0]),
    }
    team_cats = {c: sum(e["categories"][c] for e in per_engineer) for c in CATEGORIES}
    team_monthly = {m: sum(e["monthly"][m]["commits"] for e in per_engineer) for m in active_months}
    # Per-project monthly + per-engineer per-project totals for stacked charts
    team_monthly_by_project = {
        proj: {
            m: sum(
                (e["per_project"].get(proj, {}) or {}).get("monthly", {}).get(m, {}).get("commits", 0)
                for e in per_engineer
            )
            for m in active_months
        }
        for proj in REPOS.keys()
    }
    team_totals_by_project = {
        proj: {
            "total":     sum((e["per_project"].get(proj, {}) or {}).get("total", 0)     for e in per_engineer),
            "net_lines": sum((e["per_project"].get(proj, {}) or {}).get("net_lines", 0) for e in per_engineer),
        }
        for proj in REPOS.keys()
    }

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window": {"since": window["since"], "until": window["until"],
                   "range": window["range"], "year": window["year"], "label": window["label"]},
        "project": project,
        "repos": REPOS,
        "engineers": per_engineer,
        "identities": identities,
        "team_totals": team_totals,
        "team_totals_by_project": team_totals_by_project,
        "team_categories": team_cats,
        "team_monthly": team_monthly,
        "team_monthly_by_project": team_monthly_by_project,
        "categories": CATEGORIES,
        "months": active_months,
    }
    _cache_write("report", disk_key, result)
    return result


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html", "/dashboard.html"):
            html = (HERE / "dashboard.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if url.path == "/api/data":
            q = parse_qs(url.query)
            project = (q.get("project", ["ALL"])[0] or "ALL").upper()
            if project not in ("GFC", "JNC", "ALL"):
                project = "ALL"
            range_key = (q.get("range", [DEFAULT_RANGE])[0] or DEFAULT_RANGE).upper()
            year_raw = q.get("year", [None])[0]
            try:
                year = int(year_raw) if year_raw else None
            except ValueError:
                year = None
            since = q.get("since", [None])[0]
            until = q.get("until", [None])[0]
            window = resolve_window(range_key, year=year, since=since, until=until)
            force = q.get("force", ["0"])[0] in ("1", "true", "yes")
            data = build_report(project, window, force=force)
            self._send(200, json.dumps(data).encode("utf-8"), "application/json")
            return
        if url.path == "/api/identities":
            ids = resolve_identities(TEAM)
            self._send(200, json.dumps(ids).encode("utf-8"), "application/json")
            return
        if url.path == "/api/engineer_summaries":
            q = parse_qs(url.query)
            project = (q.get("project", ["ALL"])[0] or "ALL").upper()
            if project not in ("GFC", "JNC", "ALL"):
                project = "ALL"
            range_key = (q.get("range", [DEFAULT_RANGE])[0] or DEFAULT_RANGE).upper()
            year_raw = q.get("year", [None])[0]
            try:
                year = int(year_raw) if year_raw else None
            except ValueError:
                year = None
            since = q.get("since", [None])[0]
            until = q.get("until", [None])[0]
            window = resolve_window(range_key, year=year, since=since, until=until)
            identities = resolve_identities(TEAM)
            force = q.get("force", ["0"])[0] in ("1", "true", "yes")
            data = build_all_engineer_summaries(project, window, identities, force=force)
            self._send(200, json.dumps(data).encode("utf-8"), "application/json")
            return
        if url.path == "/api/team_turnins":
            q = parse_qs(url.query)
            project = (q.get("project", ["ALL"])[0] or "ALL").upper()
            if project not in ("GFC", "JNC", "ALL"):
                project = "ALL"
            range_key = (q.get("range", [DEFAULT_RANGE])[0] or DEFAULT_RANGE).upper()
            year_raw = q.get("year", [None])[0]
            try:
                year = int(year_raw) if year_raw else None
            except ValueError:
                year = None
            since = q.get("since", [None])[0]
            until = q.get("until", [None])[0]
            window = resolve_window(range_key, year=year, since=since, until=until)
            identities = resolve_identities(TEAM)
            force = q.get("force", ["0"])[0] in ("1", "true", "yes")
            data = build_team_turnin_summary(project, window, identities, force=force)
            self._send(200, json.dumps(data).encode("utf-8"), "application/json")
            return
        if url.path == "/api/turnins":
            q = parse_qs(url.query)
            engineer = (q.get("engineer", [""])[0] or "").strip()
            project = (q.get("project", ["ALL"])[0] or "ALL").upper()
            if project not in ("GFC", "JNC", "ALL"):
                project = "ALL"
            range_key = (q.get("range", [DEFAULT_RANGE])[0] or DEFAULT_RANGE).upper()
            year_raw = q.get("year", [None])[0]
            try:
                year = int(year_raw) if year_raw else None
            except ValueError:
                year = None
            since = q.get("since", [None])[0]
            until = q.get("until", [None])[0]
            window = resolve_window(range_key, year=year, since=since, until=until)
            if engineer not in TEAM:
                self._send(400, b"unknown engineer", "text/plain"); return
            identities = resolve_identities(TEAM)
            force = q.get("force", ["0"])[0] in ("1", "true", "yes")
            data = build_turnin_report(engineer, project, window, identities, force=force)
            self._send(200, json.dumps(data).encode("utf-8"), "application/json")
            return
        if url.path == "/api/diff":
            q = parse_qs(url.query)
            project = (q.get("project", ["GFC"])[0] or "GFC").upper()
            # Accept either `sha` (single) or `shas` (comma-separated fallback list)
            raw_shas = q.get("shas", [""])[0] or q.get("sha", [""])[0] or ""
            candidates = [s.strip() for s in raw_shas.split(",") if s.strip()]
            candidates = [s for s in candidates if re.match(r"^[0-9a-fA-F]{4,40}$", s)]
            file_path = (q.get("path", [""])[0] or "").strip()
            # Optional: turnin_id → enables bundle-repo fallback for in-flight turnins
            turnin_id_raw = (q.get("turnin_id", [""])[0] or "").strip()
            if project not in REPOS:
                self._send(400, b"bad project", "text/plain"); return
            if not candidates and not turnin_id_raw:
                self._send(400, b"bad sha", "text/plain"); return
            if not file_path or "\x00" in file_path:
                self._send(400, b"bad path", "text/plain"); return

            def _exists(repo_: str, sha_: str, bare: bool = False) -> bool:
                base = (["git", f"--git-dir={repo_}"] if bare else ["git", "-C", repo_])
                r = subprocess.run(base + ["cat-file", "-e", sha_ + "^{commit}"],
                                   capture_output=True, text=True, timeout=15, check=False)
                return r.returncode == 0

            def _show(repo_: str, sha_: str, extra_args: list[str], bare: bool = False):
                base = (["git", f"--git-dir={repo_}"] if bare else ["git", "-C", repo_])
                return subprocess.run(
                    base + ["--no-pager", "show",
                            "--no-color", "--pretty=format:%H%n%an%n%ad%n%s%n%n",
                            "--date=short", *extra_args, sha_, "--", file_path],
                    capture_output=True, text=True, timeout=60, check=False,
                )

            def _try_repo(repo_: str, bare: bool, label: str):
                """Return (body, chosen_sha, tried_entries)."""
                body_ = ""
                chosen_ = ""
                tried_: list[str] = []
                for s in candidates:
                    if not _exists(repo_, s, bare=bare):
                        tried_.append(f"{s[:12]}  (not in {label})")
                        continue
                    chosen_ = s
                    try:
                        out = _show(repo_, s, [], bare=bare)
                        body_ = out.stdout or ""
                        if ("diff --git" not in body_ and "diff --cc" not in body_
                                and out.returncode == 0):
                            out2 = _show(repo_, s, ["-m", "--first-parent"], bare=bare)
                            if "diff --git" in (out2.stdout or "") or "diff --cc" in (out2.stdout or ""):
                                body_ = out2.stdout
                        if not body_.strip() and out.stderr:
                            body_ = f"[git error]\n{out.stderr}"
                    except Exception as e:
                        body_ = f"error running git show: {e}"
                    if body_.strip():
                        break
                return body_, chosen_, tried_

            # 1) Try the baseline model repo (BAK_LATEST_TURNIN)
            baseline_repo = REPOS[project]
            body, chosen, tried = _try_repo(baseline_repo, bare=False, label="baseline")
            source_label = "baseline model" if body.strip() else ""

            # 2) Fallback: bundle repo under gatekeeper incoming/ (in-flight turnins)
            if not body.strip() and turnin_id_raw:
                bundle_repo = _find_bundle_repo(project, turnin_id_raw)
                if bundle_repo:
                    # Also allow the hdk_turnin<N> tag as an implicit candidate
                    # so we can still show the turnin's tip even when the shas
                    # from turnin_notes don't parse or are missing.
                    tag = f"hdk_turnin{turnin_id_raw}"
                    body_b, chosen_b, tried_b = _try_repo(bundle_repo, bare=True, label="bundle")
                    tried.extend(tried_b)
                    if not body_b.strip():
                        # try the tag as a last resort
                        r = subprocess.run(["git", f"--git-dir={bundle_repo}",
                                            "rev-parse", "--verify", tag + "^{commit}"],
                                           capture_output=True, text=True, timeout=10, check=False)
                        if r.returncode == 0:
                            sha_tag = r.stdout.strip()
                            out = _show(bundle_repo, sha_tag, [], bare=True)
                            body_b = out.stdout or ""
                            chosen_b = sha_tag
                            if body_b.strip():
                                tried.append(f"{tag} -> {sha_tag[:12]}  (bundle tag)")
                    if body_b.strip():
                        body = body_b
                        chosen = chosen_b
                        source_label = f"bundle repo (in-flight turnin {turnin_id_raw})"
                else:
                    tried.append(f"user_turnin{turnin_id_raw}  (bundle repo not found)")

            if not body.strip():
                extra = "\n".join(tried) if tried else "(none)"
                body = ("This turnin's commits are not present locally.\n"
                        f"Baseline repo:  {baseline_repo}\n"
                        f"Bundle search:  {INCOMING_GLOBS.get(project, '').format(tid=turnin_id_raw or '<id>')}\n\n"
                        f"Candidates tried:\n{extra}\n\n"
                        "This usually means the turnin was rejected/cancelled,\n"
                        "the bundle has already been purged from incoming/, or\n"
                        "the commits landed after the local model bundle was cut.\n"
                        "Check the code review URL (if any) for the change details.")
            elif chosen:
                body = f"# resolved sha: {chosen}  [{source_label}]\n" + body
            self._send(200, body.encode("utf-8", errors="replace"), "text/plain; charset=utf-8")
            return
        if url.path == "/api/health":
            self._send(200, b'{"ok":true}', "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802 - quieter logs
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    print(f"Repos: {REPOS}")
    print(f"Default range: {DEFAULT_RANGE} (override per-request via ?range=YTD|MTD|H1|H2|FY|CUSTOM&year=YYYY)")
    print(f"Team ({len(TEAM)}): {', '.join(TEAM)}")
    print(f"Serving on http://0.0.0.0:{PORT}/  (Ctrl-C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
