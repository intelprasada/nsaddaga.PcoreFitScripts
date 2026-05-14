#!/usr/bin/env python3
"""Guard against module-resolution hazards: a tracked file `X.<ext>` sitting
next to a tracked directory `X/` causes Vite (and many other resolvers) to
silently pick the file over the directory's `index.*`. This has bitten the
repo three times via empty-merge / partial-rename mishaps (#205, #208, …).

Exit status:
  0 — no collisions found
  1 — at least one collision; offending pairs printed to stderr.

Run from the repo root (or any subdir — uses `git ls-files`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections import defaultdict


# Extensions where the file-vs-dir-index ambiguity actually breaks resolvers.
# Add more if needed — these cover JS/TS module resolution + Python packages.
RISKY_EXTS = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".json", ".css", ".scss",
    ".py",
}

# Ignore vendored / generated trees (defensive — git ls-files normally skips
# .gitignored content, but keep the guard tight even if someone tracks them).
IGNORE_PREFIXES = ("node_modules/", "VegaNotes/node_modules/", "dist/", "build/")


def list_tracked() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files"], text=True, stderr=subprocess.DEVNULL,
    )
    return [p for p in out.splitlines() if p and not p.startswith(IGNORE_PREFIXES)]


def find_collisions(paths: list[str]) -> list[tuple[str, str]]:
    """Return list of (file_path, dir_path) collision pairs."""
    # Map (parent_dir, basename_no_ext) -> {"files": [...], "is_dir_root": bool}
    by_parent: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"files": [], "is_dir_root": False},
    )
    dir_seen: set[tuple[str, str]] = set()

    for p in paths:
        parent, name = os.path.split(p)
        # Detect that `parent`'s last segment is itself a tracked-dir name —
        # any file inside `<parent>/` proves the dir exists.
        if "/" in parent:
            grand, dirname = parent.rsplit("/", 1)
        else:
            grand, dirname = "", parent
        if dirname:
            dir_seen.add((grand, dirname))
        # Track this file as a candidate (only if it has a risky extension).
        base, ext = os.path.splitext(name)
        if ext.lower() in RISKY_EXTS and base:
            by_parent[(parent, base)]["files"].append(p)

    collisions: list[tuple[str, str]] = []
    for (parent, base), info in by_parent.items():
        if (parent, base) in dir_seen:
            for f in info["files"]:
                dir_path = f"{parent}/{base}" if parent else base
                collisions.append((f, dir_path))
    collisions.sort()
    return collisions


def main() -> int:
    paths = list_tracked()
    collisions = find_collisions(paths)
    if not collisions:
        return 0
    print(
        "ERROR: Found tracked file(s) that shadow a sibling directory's "
        "module index:\n", file=sys.stderr,
    )
    for f, d in collisions:
        print(f"  {f}\n    collides with directory: {d}/\n", file=sys.stderr)
    print(
        "Why this matters: Vite / TS / Python module resolvers prefer the "
        "file over `<dir>/index.*`, so the directory's exports become "
        "unreachable at runtime.\n"
        "Fix: delete the flat file (or rename one of the two).\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
