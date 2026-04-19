"""Force a full reindex of notes/ → SQLite. Useful after PVC restore."""
from __future__ import annotations
from app.db import ensure_data_dirs, init_db, session_scope
from app.indexer import reindex_all


def main() -> None:
    ensure_data_dirs()
    init_db()
    with session_scope() as s:
        n = reindex_all(s)
    print(f"reindexed {n} files")


if __name__ == "__main__":
    main()
