from __future__ import annotations

from pathlib import Path
from typing import Any

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

    @property
    def notes_dir(self) -> Path:
        return self.data_dir / self.notes_subdir

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename


settings = Settings()
