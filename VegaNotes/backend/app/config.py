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

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / self.notes_subdir

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename


settings = Settings()
