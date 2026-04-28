"""Safe filesystem I/O for note files.

This module is the single point of truth for **every** write that lands in
``settings.notes_dir``. It exists because the previous direct ``write_text``
call sites had no concurrency control and silently overwrote newer disk
content with stale client buffers — see issue #60 (P0 data-loss).

Three guarantees provided here:

1.  **Optimistic concurrency.** Callers may pass ``expected_etag``; if the
    file's current etag (sha256 of bytes) does not match, ``StaleWriteError``
    is raised carrying the *current* content + etag so the caller can return
    a 409 with a useful payload for the client to reconcile.

2.  **Atomic write + per-file lock.** Writes go to a sibling ``.tmp`` file
    and are then ``os.replace``-d into place under a ``threading.Lock`` keyed
    on the resolved path. Two concurrent writes on the same file serialize
    cleanly; two writes on different files don't block each other.

3.  **Defense-in-depth backup.** Before each write, the previous on-disk
    content is copied into ``<notes_dir>/.trash/<relpath>.<ts>.bak`` (UTC
    timestamp). Cheap insurance against any future bug, and lets users
    recover by hand without admin intervention.
"""
from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class StaleWriteError(Exception):
    """Raised by :func:`safe_write` when ``expected_etag`` doesn't match
    the file's current etag. Carries the current bytes + etag so the
    HTTP layer can return a 409 with a body the client can reconcile.
    """

    def __init__(self, *, current_content: str, current_etag: str) -> None:
        super().__init__("stale write: file changed under you")
        self.current_content = current_content
        self.current_etag = current_etag


_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """Return a process-wide :class:`threading.Lock` keyed on ``path``.

    Locks are created on demand and never evicted — the working set of
    note paths is small (~hundreds) and a stale entry costs ~56 bytes.
    """
    key = path.resolve()
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


def etag_for(path: Path) -> str:
    """Return the sha256 hex digest of ``path``'s bytes, or the empty
    string if the file does not exist.

    The empty-string sentinel lets clients send ``If-Match: ""`` to mean
    "I expect this path to be new" — the create case.
    """
    try:
        with path.open("rb") as f:
            h = hashlib.sha256()
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
            return h.hexdigest()
    except FileNotFoundError:
        return ""


def etag_for_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_path(notes_dir: Path, target: Path) -> Path:
    """Compute the ``.trash`` path that mirrors ``target`` and appends a
    UTC timestamp + ``.bak`` suffix.
    """
    rel = target.resolve().relative_to(notes_dir.resolve())
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return notes_dir / ".trash" / f"{rel}.{ts}.bak"


def _normalize_for_disk(path: Path, content: str) -> str:
    """Apply on-write content normalization for note files.

    Currently: enforce tabs-only indentation (1 tab == 4 spaces) for ``.md``
    files. Indent levels drive parent/child relationships throughout the
    tool, so a single canonical indent style on disk avoids subtle bugs
    where mixed tab/space indents misaligned. Non-``.md`` files pass
    through unchanged.

    Lazy-imports the markdown_ops helper to avoid a top-level circular
    dependency (markdown_ops -> parser -> safe_io in some call paths).
    """
    if path.suffix.lower() != ".md":
        return content
    from .markdown_ops import normalize_indent_to_tabs
    return normalize_indent_to_tabs(content)


def safe_write(
    path: Path,
    content: str,
    *,
    notes_dir: Path,
    expected_etag: Optional[str] = None,
    encoding: str = "utf-8",
) -> str:
    """Write ``content`` to ``path`` with concurrency check, backup, and
    atomic replace. Returns the new etag.

    ``expected_etag``:
        - ``None``: skip the concurrency check (caller takes responsibility,
          e.g. for purely additive PATCHes that re-read inside the lock).
        - ``""``  : require the file to NOT currently exist.
        - else   : require the file's current etag to match exactly.

    Raises :class:`StaleWriteError` on a mismatch.
    """
    content = _normalize_for_disk(path, content)
    lock = _lock_for(path)
    with lock:
        if expected_etag is not None:
            cur_etag = etag_for(path)
            if cur_etag != expected_etag:
                cur_text = path.read_text(encoding=encoding) if cur_etag else ""
                raise StaleWriteError(
                    current_content=cur_text, current_etag=cur_etag,
                )

        # Backup pre-image (skipped on first creation).
        if path.exists():
            bp = _backup_path(notes_dir, path)
            bp.parent.mkdir(parents=True, exist_ok=True)
            # shutil.copy2 preserves mtime; use raw copy of bytes for speed.
            bp.write_bytes(path.read_bytes())

        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp file in same dir (so os.replace is rename, not copy)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            tmp.write_text(content, encoding=encoding)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return etag_for_bytes(content.encode(encoding))


def read_under_lock(path: Path, *, encoding: str = "utf-8") -> tuple[str, str]:
    """Read ``path`` while holding its write lock. Returns ``(content, etag)``.

    Use this in PATCH-style code paths that read-modify-write so the
    contents observed are the same ones the eventual write will check
    against.

    Note: callers that do their own ``safe_write`` afterwards must NOT
    re-acquire the lock (the locks here are not reentrant). Prefer
    :func:`with_file_lock` when the same handler does both read and write.
    """
    lock = _lock_for(path)
    with lock:
        text = path.read_text(encoding=encoding) if path.exists() else ""
        return text, etag_for_bytes(text.encode(encoding))


class with_file_lock:  # noqa: N801 — used as a context manager
    """Context manager that holds the per-file write lock for the body.

    Inside the ``with`` block the caller may freely read+mutate+write; no
    other writer on the same path will interleave. The body is responsible
    for the actual write (typically via ``_safe_write_unlocked``).
    """

    def __init__(self, path: Path) -> None:
        self._lock = _lock_for(path)

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *_exc: object) -> None:
        self._lock.release()


def _safe_write_unlocked(
    path: Path,
    content: str,
    *,
    notes_dir: Path,
    expected_etag: Optional[str] = None,
    encoding: str = "utf-8",
) -> str:
    """Same contract as :func:`safe_write` but assumes the caller already
    holds the per-file lock (via :class:`with_file_lock`). Used by handlers
    that do read-modify-write in one critical section.
    """
    content = _normalize_for_disk(path, content)
    if expected_etag is not None:
        cur_etag = etag_for(path)
        if cur_etag != expected_etag:
            cur_text = path.read_text(encoding=encoding) if cur_etag else ""
            raise StaleWriteError(
                current_content=cur_text, current_etag=cur_etag,
            )
    if path.exists():
        bp = _backup_path(notes_dir, path)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return etag_for_bytes(content.encode(encoding))
