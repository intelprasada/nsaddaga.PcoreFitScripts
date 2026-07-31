# teamhub — Live Team Performance Dashboard

A stdlib-only Python HTTP server that serves a single-page live dashboard
summarizing turnin, commit, and HSD activity for a manager's team across
the **GFC** and **JNC** projects.

- Zero third-party runtime dependencies (Python 3.9+ standard library only).
- Data is recomputed on demand from live git repos and the `turnininfo`
  Gatekeeper tool. All results are cached under `tools/teamhub/.cache/` so
  routine refreshes are instant; a "⟳ Force refetch" button in the UI
  bypasses every cache tier.
- Charts rendered client-side with Chart.js (loaded from CDN).

## Quick start

```tcsh
# Start the server (foreground)
bin/teamhub

# ...or background it with a log
nohup bin/teamhub > /tmp/teamhub.log 2>&1 &
```

Then open `http://<hostname>:8765/` in your browser.

## Tabs

| Tab | Contents |
|-----|----------|
| **Team Overview** | Per-engineer bars/pies for commits, files touched, and net line changes; per-month team totals. |
| **Engineer Detail** | Drill-in view for a selected engineer — files modified, monthly trend, top commits. |
| **Turnins (TI)** | Per-TI table with status, HSDs filed, pipe age (time-to-release), and files-changed breakdown; per-engineer and team-wide TTR KPIs. |
| **Engineer Summary** | Auto-generated per-engineer cards: unit classification (STSR / IDQ / DSB / DSBE / MS-MSID / IFU / BPU / BAC / IDU / IQ / RAT / MCA / FV-FPV / CTE), activity mix (Coverage / Emulation debug / Assertion tuning / Bug fix / Feature enablement / Cleanup), top files, and clickable TI-id chips that navigate to the TI tab. |

Every tab supports an anonymize selector (Real names / Marvel / DC / Anonymous IDs) applied to all charts, tables, and card titles.

## CLI options

The server itself takes no CLI flags; all configuration is via environment
variables:

| Env var | Default | Description |
|---------|---------|-------------|
| `DASH_PORT` | `8765` | HTTP port to bind (binds on `0.0.0.0`). |
| `SINCE` | `2026-01-01` | `git --since` cutoff for the initial window. |
| `UNTIL` | `2026-06-30` | `git --until` cutoff for the initial window. |
| `GFC_REPO` | newest `/nfs/site/proj/gfc/gfc.models.*/core/core-gfc-b0-master-*` (matches `setGFC`) | Override GFC repo path. |
| `JNC_REPO` | `/p/hdk/rtl/proj_data/xhdk74/bak_latest_turnins/jnc/fit/fit-jnc-a0-master-latest` | Override JNC repo path. |
| `TEAM` | built-in roster | Comma-separated author names override. |
| `TURNIN_TTL` | `86400` | Seconds to cache `turnininfo` output per user. |
| `GIT_REPORT_TTL` | `86400` | Seconds to cache the git report for a live window. |
| `IDSID_CACHE` | `<tool>/.idsid_cache.json` | Path to the idsid → real-name mapping cache. |

## HTTP API

Everything below returns JSON. Add `?force=1` to bypass all cache tiers
(bypasses on-disk cache and re-hits git / turnininfo).

| Endpoint | Description |
|----------|-------------|
| `GET /` | The single-page dashboard (HTML). |
| `GET /api/data?project=GFC\|JNC\|ALL&range=H1&year=2026` | Per-engineer commit / file / churn / monthly report. |
| `GET /api/turnins?project=…&engineer=…` | Turnins for one engineer with pipe-age and HSD list. |
| `GET /api/team_turnins?project=…` | Team-wide turnin leaderboard with TTR percentiles. |
| `GET /api/engineer_summaries?project=…` | Aggregated unit / activity classification for every engineer. |
| `GET /api/health` | Liveness probe. |

## Prose summary generator

`gen_prose_summaries.py` reads the cached `/api/engineer_summaries` payload
and emits a per-engineer Markdown narrative. Run it after the dashboard has
loaded the Engineer Summary tab at least once:

```tcsh
python3 tools/teamhub/gen_prose_summaries.py   # writes H1_2026_engineer_summaries.md
```

The generated Markdown groups each engineer's turnins by RTL unit with an
activity mix and the top 5 files touched.

## Requirements

- Python 3.9+ (standard library only — no `pip install` needed).
- Read access to the GFC and JNC repositories and the `turnininfo`
  Gatekeeper tool (auto-sources the corresponding project HDK env).
- A modern browser for viewing the dashboard.

## Cache

Everything cached under `tools/teamhub/.cache/`. Delete a file to force a
refetch on the next request, or use the "⟳ Force refetch" button in the UI.

| Prefix | Contents |
|--------|----------|
| `report__<proj>_<since>_<until>.json` | Per-project git report. |
| `team_turnins__<proj>_<since>_<until>.json` | Team-wide turnin summary. |
| `turnins_raw__<proj>_<idsid>.json` | Raw `turnininfo` output per user. |
| `summaries__<proj>_<since>_<until>.json` | Aggregated Engineer Summary data. |

Cache files are ignored by git (see repo-root `.gitignore`).
