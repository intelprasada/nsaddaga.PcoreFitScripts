"""Tests for watcher mode selection + polling fallback (#150)."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Configure data dir before importing the app modules so settings binds early.
_TMP = Path(tempfile.mkdtemp(prefix="vega-watcher-"))
os.environ.setdefault("VEGANOTES_DATA_DIR", str(_TMP))

from app import indexer  # noqa: E402
from app.config import Settings  # noqa: E402


# --- _detect_fs_type ------------------------------------------------------

def _write_mounts(tmp_path: Path, lines: list[str]) -> Path:
    f = tmp_path / "mounts"
    f.write_text("\n".join(lines) + "\n")
    return f


def test_detect_fs_type_nfs_from_proc_mounts(tmp_path):
    mounts = _write_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw,relatime 0 0",
        "scc96n07a-1:/nsaddaga_wa /nfs/site/disks/nsaddaga_wa nfs rw 0 0",
        "tmpfs /run tmpfs rw 0 0",
    ])
    fs = indexer._detect_fs_type(
        Path("/nfs/site/disks/nsaddaga_wa/some/sub/dir"),
        mounts_file=str(mounts),
    )
    assert fs == "nfs"


def test_detect_fs_type_picks_longest_prefix(tmp_path):
    mounts = _write_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw 0 0",
        "/dev/sdb1 /home ext4 rw 0 0",
        "scc96:/share /home/user/notes nfs4 rw 0 0",
    ])
    fs = indexer._detect_fs_type(
        Path("/home/user/notes/sub"),
        mounts_file=str(mounts),
    )
    assert fs == "nfs4"


def test_detect_fs_type_local(tmp_path):
    mounts = _write_mounts(tmp_path, [
        "/dev/sda1 / ext4 rw 0 0",
    ])
    fs = indexer._detect_fs_type(tmp_path, mounts_file=str(mounts))
    assert fs == "ext4"


def test_detect_fs_type_missing_file_returns_none(tmp_path):
    assert indexer._detect_fs_type(tmp_path, mounts_file=str(tmp_path / "nope")) is None


# --- _compute_force_polling ----------------------------------------------

def test_compute_force_polling_explicit_true(monkeypatch, tmp_path):
    monkeypatch.setattr(indexer.settings, "watcher_force_polling", True)
    force, _ = indexer._compute_force_polling(tmp_path)
    assert force is True


def test_compute_force_polling_explicit_false(monkeypatch, tmp_path):
    monkeypatch.setattr(indexer.settings, "watcher_force_polling", False)
    monkeypatch.setattr(indexer, "_detect_fs_type", lambda *_a, **_k: "nfs")
    force, fs = indexer._compute_force_polling(tmp_path)
    assert force is False
    assert fs == "nfs"


def test_compute_force_polling_auto_nfs(monkeypatch, tmp_path):
    monkeypatch.setattr(indexer.settings, "watcher_force_polling", None)
    monkeypatch.setattr(indexer, "_detect_fs_type", lambda *_a, **_k: "nfs")
    force, fs = indexer._compute_force_polling(tmp_path)
    assert force is True
    assert fs == "nfs"


def test_compute_force_polling_auto_local(monkeypatch, tmp_path):
    monkeypatch.setattr(indexer.settings, "watcher_force_polling", None)
    monkeypatch.setattr(indexer, "_detect_fs_type", lambda *_a, **_k: "ext4")
    force, fs = indexer._compute_force_polling(tmp_path)
    assert force is False
    assert fs == "ext4"


def test_compute_force_polling_auto_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(indexer.settings, "watcher_force_polling", None)
    monkeypatch.setattr(indexer, "_detect_fs_type", lambda *_a, **_k: None)
    force, _ = indexer._compute_force_polling(tmp_path)
    assert force is False


# --- Settings env var binding --------------------------------------------

def test_settings_reads_watcher_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VEGANOTES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VEGANOTES_WATCHER_FORCE_POLLING", "true")
    monkeypatch.setenv("VEGANOTES_WATCHER_POLL_DELAY_MS", "777")
    s = Settings()
    assert s.watcher_force_polling is True
    assert s.watcher_poll_delay_ms == 777


def test_settings_default_watcher(monkeypatch, tmp_path):
    monkeypatch.setenv("VEGANOTES_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VEGANOTES_WATCHER_FORCE_POLLING", raising=False)
    monkeypatch.delenv("VEGANOTES_WATCHER_POLL_DELAY_MS", raising=False)
    s = Settings()
    assert s.watcher_force_polling is None
    assert s.watcher_poll_delay_ms == 2000


# --- Live polling round-trip (proves the fix on NFS) ---------------------

def test_polling_awatch_observes_external_change(tmp_path):
    """Real ``awatch(force_polling=True)`` must surface a change made via a
    plain ``write_text`` on a directory that may not deliver inotify events.
    This simulates the NFS condition: with polling on, the change is seen.
    """
    from watchfiles import awatch

    target = tmp_path / "probe.md"
    target.write_text("seed\n")

    async def run() -> list[str]:
        seen: list[str] = []

        async def writer():
            await asyncio.sleep(0.6)
            target.write_text("changed\n")

        async def reader():
            async for changes in awatch(
                tmp_path, force_polling=True, poll_delay_ms=200, debounce=300,
            ):
                for _change, p in changes:
                    seen.append(p)
                if seen:
                    return

        await asyncio.wait_for(asyncio.gather(reader(), writer()), timeout=20)
        return seen

    seen = asyncio.run(run())
    assert any("probe.md" in p for p in seen), seen
