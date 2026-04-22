"""Tests for app.safe_io — issue #60.

Covers: etag stability, atomic write, per-file lock serialization,
StaleWriteError on If-Match mismatch, .trash backup creation.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.safe_io import (
    StaleWriteError, etag_for, etag_for_bytes, safe_write, with_file_lock,
    _safe_write_unlocked,
)


def test_etag_empty_for_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "nope.md"
    assert etag_for(p) == ""


def test_etag_matches_content(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("hello")
    assert etag_for(p) == etag_for_bytes(b"hello")


def test_safe_write_creates_file_and_returns_etag(tmp_path: Path) -> None:
    p = tmp_path / "notes" / "a.md"
    e = safe_write(p, "first", notes_dir=tmp_path)
    assert p.read_text() == "first"
    assert e == etag_for_bytes(b"first")


def test_safe_write_overwrites_and_backs_up(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("v1")
    safe_write(p, "v2", notes_dir=tmp_path)
    assert p.read_text() == "v2"
    # A .trash/<name>.<ts>.bak file containing v1 must exist.
    backups = list((tmp_path / ".trash").glob("a.md.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "v1"


def test_safe_write_no_backup_on_first_write(tmp_path: Path) -> None:
    p = tmp_path / "fresh.md"
    safe_write(p, "hello", notes_dir=tmp_path)
    assert not (tmp_path / ".trash").exists()


def test_safe_write_if_match_mismatch_raises(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("server-version")
    with pytest.raises(StaleWriteError) as ei:
        safe_write(p, "client-stale", notes_dir=tmp_path,
                   expected_etag="0" * 64)
    assert ei.value.current_content == "server-version"
    assert ei.value.current_etag == etag_for_bytes(b"server-version")
    # File must NOT have been modified.
    assert p.read_text() == "server-version"
    # No backup either, because the write was rejected before that step.
    assert not (tmp_path / ".trash").exists()


def test_safe_write_if_match_matches_succeeds(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("v1")
    e1 = etag_for(p)
    e2 = safe_write(p, "v2", notes_dir=tmp_path, expected_etag=e1)
    assert p.read_text() == "v2"
    assert e2 == etag_for_bytes(b"v2")


def test_safe_write_if_match_empty_for_create(tmp_path: Path) -> None:
    p = tmp_path / "new.md"
    # Empty etag means "expect file not to exist".
    safe_write(p, "fresh", notes_dir=tmp_path, expected_etag="")
    assert p.read_text() == "fresh"


def test_safe_write_if_match_empty_rejects_existing(tmp_path: Path) -> None:
    p = tmp_path / "exists.md"
    p.write_text("already here")
    with pytest.raises(StaleWriteError):
        safe_write(p, "create-attempt", notes_dir=tmp_path, expected_etag="")
    assert p.read_text() == "already here"


def test_safe_write_atomic_replace_no_partial(tmp_path: Path) -> None:
    """Sanity: the .tmp file isn't left lying around after a successful write."""
    p = tmp_path / "a.md"
    safe_write(p, "v1", notes_dir=tmp_path)
    safe_write(p, "v2", notes_dir=tmp_path)
    leftovers = list(tmp_path.glob(".a.md.tmp.*"))
    assert leftovers == []


def test_per_file_lock_serializes_concurrent_writers(tmp_path: Path) -> None:
    """Two threads writing the same path produce a deterministic final state
    (one of the two writes), not a corrupted interleave. We verify by
    checking that the file ends up with one of the two payloads in full.
    """
    p = tmp_path / "race.md"
    p.write_text("seed")
    payloads = [f"thread-{i}-" + ("x" * 4096) for i in range(2)]
    barrier = threading.Barrier(2)

    def worker(i: int) -> None:
        barrier.wait()
        safe_write(p, payloads[i], notes_dir=tmp_path)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()

    final = p.read_text()
    assert final in payloads, f"interleaved write detected: {final[:80]!r}"


def test_with_file_lock_blocks_concurrent_safe_write(tmp_path: Path) -> None:
    """A thread holding ``with_file_lock`` keeps another thread's safe_write
    waiting until the block exits.
    """
    p = tmp_path / "a.md"
    p.write_text("v1")
    started = threading.Event()
    done = threading.Event()
    holder_releases = threading.Event()

    def holder() -> None:
        with with_file_lock(p):
            started.set()
            holder_releases.wait(timeout=2)

    def writer() -> None:
        safe_write(p, "v2", notes_dir=tmp_path)
        done.set()

    h = threading.Thread(target=holder); h.start()
    started.wait(timeout=1)
    w = threading.Thread(target=writer); w.start()
    # Writer should be blocked while holder still has the lock.
    assert not done.wait(timeout=0.2)
    holder_releases.set()
    h.join(); w.join()
    assert done.is_set()
    assert p.read_text() == "v2"


def test_safe_write_unlocked_inside_with_file_lock(tmp_path: Path) -> None:
    """The unlocked variant works inside an explicit lock and triggers the
    same backup + atomic semantics.
    """
    p = tmp_path / "a.md"
    p.write_text("v1")
    with with_file_lock(p):
        cur = p.read_text()
        assert cur == "v1"
        new_etag = _safe_write_unlocked(p, "v2", notes_dir=tmp_path)
    assert p.read_text() == "v2"
    assert new_etag == etag_for_bytes(b"v2")
    backups = list((tmp_path / ".trash").glob("a.md.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "v1"


def test_safe_write_unlocked_if_match_mismatch_raises(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("v1")
    with with_file_lock(p):
        with pytest.raises(StaleWriteError):
            _safe_write_unlocked(p, "v2", notes_dir=tmp_path,
                                  expected_etag="badetag")
    assert p.read_text() == "v1"


def test_backup_path_preserves_subdir_structure(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "dir" / "a.md"
    p.parent.mkdir(parents=True)
    p.write_text("v1")
    safe_write(p, "v2", notes_dir=tmp_path)
    backups = list((tmp_path / ".trash" / "sub" / "dir").glob("a.md.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "v1"
