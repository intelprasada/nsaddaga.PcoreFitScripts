from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VEGANOTES_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("/data"))
    notes_subdir: str = "notes"
    db_filename: str = "index.sqlite"
    # #304: sibling read-only(-ish) history DB. Populated by user-driven
    # archive (POST /api/notes/{id}/archive) and drained by unarchive.
    # Same schema as the main DB minus user-scoped tables. Kept in the
    # same ``data_dir`` alongside ``db_filename``.
    archive_db_filename: str = "archive.sqlite"
    timezone: str = "UTC"
    agenda_window_days: int = 7

    basic_auth_user: str = "admin"
    # bcrypt hash of "admin" (CHANGE IN PRODUCTION via VEGANOTES_BASIC_AUTH_PASS_HASH).
    basic_auth_pass_hash: str = (
        "$2b$12$0gTw4ro9xQdfb/D.Wcaay.vFD3lm6vgl5EvssXJ2p/UUt8XjPAtFy"
    )

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    serve_static: bool = True   # single-pod mode
    static_dir: Path = Field(default=Path(__file__).parent / "static")

    # Token vocab override (loaded from ConfigMap as JSON if present)
    token_vocab: dict[str, Any] = Field(default_factory=dict)

    # File watcher tuning (#150).  ``None`` for force_polling means
    # auto-detect: enable polling when notes_dir lives on a filesystem that
    # doesn't deliver inotify events (NFS, SMB, FUSE network mounts).
    watcher_force_polling: Optional[bool] = None
    watcher_poll_delay_ms: int = 2000

    # Intel Phonebook scraper (#213) — tier-3 owner-token resolver.
    # Defaults to OFF so K8s deployments behind firewalls never make
    # outbound calls unless explicitly opted in.
    phonebook_scraper_enabled: bool = False
    phonebook_scraper_url: str = "https://phonebook.intel.com"
    phonebook_scraper_timeout_s: float = 8.0
    phonebook_cache_ttl_s: int = 86400
    # Org-distance ranking anchor used when neither the request nor the
    # authenticated user resolves to an Intel idsid (#215). For local
    # dev where you log in as ``admin``, set this to your idsid so
    # bare-name owner tokens like ``@Niharika`` still rank against your
    # subtree instead of returning ambiguous.
    phonebook_default_anchor: str | None = None
    # Penalty per level of seniority delta in the phonebook ranker (#224).
    # `score = raw_distance + (up - down) * penalty`. Higher = stronger
    # preference for peers/reportees over senior leaders. 0 = back to
    # the original symmetric LCA distance.
    phonebook_seniority_penalty: int = 3
    # Score delta applied when a candidate's manager-chain passes through
    # any of the anchor's preferred subtrees (#225). Negative discounts;
    # a -5 default is large enough to break ties (which are typically 1-3
    # apart on score) but small enough that a far-away non-preferred
    # candidate doesn't beat a near-perfect non-preferred match.
    phonebook_subtree_bias: int = -5

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / self.notes_subdir

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @property
    def archive_db_path(self) -> Path:
        return self.data_dir / self.archive_db_filename


settings = Settings()
