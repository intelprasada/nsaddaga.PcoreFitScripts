"""Seed a few sample markdown notes into VEGANOTES_DATA_DIR/notes."""
from __future__ import annotations
import os
from pathlib import Path

NOTES = {
    "sprint14.md": """# Sprint 14

- !task Wire up SSO #project veganotes #owner alice #eta +5d #priority P1
  - !task Add login screen #owner bob #eta +6d
  - !task Add OAuth callback #owner alice #eta +7d #status in-progress
- !task Migrate index #feature search-rewrite #owner alice #status done
  Blocked by #task wire-up-sso and tracked in #link rfc-2026-04
""",
    "ops/runbook.md": """# Ops runbook

- !task Rotate DB credentials #owner ops #project platform #priority P0 #eta tomorrow
- !task Backup PVC weekly #owner ops #project platform #estimate 1h
""",
}


def main() -> None:
    base = Path(os.environ.get("VEGANOTES_DATA_DIR", "./.devdata")) / "notes"
    base.mkdir(parents=True, exist_ok=True)
    for rel, body in NOTES.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        print(f"seeded {p}")


if __name__ == "__main__":
    main()
