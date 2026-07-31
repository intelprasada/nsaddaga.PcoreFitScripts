# Prompt — Live Team Performance Dashboard (GFC + JNC, H1 2026)

Use this file as the single source of truth to (re)generate the H1 2026
team-performance dashboard for my direct reports.

---

## 1. Goal

Deliver a **professional, interactive HTML dashboard** that visualizes the
productivity and work patterns of my direct reports for **H1 2026**,
restricted to the **GFC** and **JNC** projects. The dashboard must include a
**project drop-down** (GFC / JNC / Both) and must **refresh live** — every
browser refresh (and every click of the Refresh button) must re-mine the
underlying git repositories and rebuild all metrics from scratch (no cached
data).

---

## 2. Data Sources

### 2.1 Repositories
- **GFC** — reached via `setGFC`, which now uses **`gfc-b0`** (not `gfc-a0`).
  Default working copy for the dashboard:
  `/nfs/site/proj/gfc/gfc.models.34/core/core-gfc-b0-master-26ww29a`
  (override via `GFC_REPO` env var; prefer the newest `core-gfc-b0-master-*`
  bundle under `/nfs/site/proj/gfc/gfc.models.*/core/`).
- **JNC** — reached via `setJNCfit` (`-m fit -s jnc-a0 -b master`). Default
  working copy: `/nfs/site/disks/nsaddaga_wa/JNC_2` (override via `JNC_REPO`).

### 2.2 Team roster (my direct reports)
Use **`phonebook -c BookName -c IDSID -c WWID -c MgrWWID -d MgrWWID 11342477`**
to enumerate current reportees (my WWID is `11342477`). Do **not** reuse the
2025 CSV roster — the team has changed.

Current phonebook result (as of 2026-07-14) + explicit inclusions:
1. Gautham Ajith
2. Kushwanth Bandanadham
3. Sachin Bhattad
4. Kelsey Byers
5. Namratha Jammalamadugu
6. Muana Kasongo
7. Yongxi Li
8. Ragavi Nagarathinam
9. Aboli Sawant
10. Akash Kumar Vruddhula
11. **Niharika Chatla** *(explicitly included)*
12. **Edwin Mendez Valverde** *(explicitly included)*

### 2.3 Format templates (for CSV / MD exports only)
- `../perf2025/efficiency_work_patterns_2025.csv`
- `../perf2025/ragavi_nagarathinam_performance_review_2025.md`

---

## 3. Metrics (per engineer, per project)

Mine `git log --numstat` in the selected repo(s) for
`SINCE=2026-01-01 .. UNTIL=2026-06-30` and compute:

- Total commits
- Net lines changed (`+` minus `-`)
- Average lines/commit, median lines/commit
- Commits at or below the median, and the corresponding %
- Monthly commit distribution (Jan..Jun)
- Commit classification by subject-line keyword (order matters — first match wins):
  1. `Bug Fixes` — `bug|fix|bugtrack|hotfix`
  2. `Feature Implementations` — `feature|add|implement|new|enable|support`
  3. `CTE Updates` — `cte`
  4. `Coverage Improvements` — `cov|coverage`
  5. `Code Quality` — `lint|cleanup|clean up|rename|refactor|comment|typo|format`
  6. `Other` — anything else
- Derived "Work Pattern" label using the same buckets as the 2025 CSV
  (large / mix / medium / small / minimal).
- Author matching is case-insensitive and tolerant of both `"Last, First"`
  and `"First Last"` variants.

---

## 4. Deliverables

### 4.1 `dashboard_server.py`
- Python 3, **stdlib only** (`http.server`, `subprocess`, `json`, `re`,
  `statistics`).
- Re-runs `git log` on every `/api/data?project=GFC|JNC|ALL` request — no
  caching, so a browser refresh always yields real-time data.
- Serves `dashboard.html` at `/` and JSON at `/api/data`.
- Configurable via env vars: `GFC_REPO`, `JNC_REPO`, `DASH_PORT`, `SINCE`,
  `UNTIL`, `TEAM` (comma-separated names).
- Uses **`ThreadingHTTPServer`** for concurrent requests.
- Emits `Cache-Control: no-store` on all responses.

### 4.2 `dashboard.html` — visual/UX requirements
- **Single file**, Chart.js loaded from a CDN (`chart.js@4.4.x`), Inter web
  font from Google Fonts.
- **Project drop-down** (`GFC`, `JNC`, `Both`) that reloads data on change.
- Explicit **"↻ Refresh"** button that re-fetches from `/api/data`.
- Shows a "last updated" timestamp after each fetch.
- **Live refresh**: browser refresh must trigger a fresh git mining pass.

**Layout**
- Sticky glass header (backdrop-blur) with title, data window, project
  selector, refresh, status.
- KPI cards row: Project, Active Engineers, Total Commits, Net Lines, Data
  Window, Generated timestamp.
- Row 1 grid (2:1): "Commits per Engineer" bar chart | "Commit-Type Mix
  (Team)" doughnut.
- Row 2 grid (1:1): "Net Lines per Engineer" bar chart | "Monthly Commit
  Distribution (Team)" bar chart.
- Row 3 grid (1:1): Per-engineer table (clickable rows) | Per-engineer
  doughnut for the currently selected row.

**Typography — MUST**
- **Minimum font size = 12px anywhere in the UI** (CSS and Chart.js).
- All other font sizes scale up from 12px (e.g., 13/14/15/20/28).
- Set `Chart.defaults.font.size = 12` and pass explicit
  `ticks.font.size = 12` on every axis so axis tick labels and legends are
  also ≥12px.
- Font family: `Inter` with system-font fallback, antialiased.

**Color scheme — professional, dashboard-quality**
- Dark theme, high contrast, subtle radial gradient background.
- Palette variables:
  `--bg #0b1220`, `--panel #131d38`, `--panel2 #1a2647`,
  `--ink #eef2ff`, `--mute #93a3c9`, `--border #23325a`,
  `--accent #60a5fa`.
- Curated 12-color chart palette (Observable/Tableau feel):
  `#60a5fa, #34d399, #fbbf24, #f87171, #a78bfa, #22d3ee,
   #fb923c, #f472b6, #a3e635, #38bdf8, #fb7185, #94a3b8`.
- Bars: rounded (6px), 80% alpha fill with full-alpha hover.
- Doughnuts: 58% cutout, 2px panel-colored gap, tooltip shows count + %.
- Cards: soft shadow, 1px border, inner-highlight for depth.

### 4.3 Exports
- `efficiency_work_patterns_H12026.csv` — same schema as the 2025 CSV.
- `<engineer>_performance_review_H12026.md` for each team member using the
  2025 review template.

---

## 5. Constraints

- Read-only access to git repos; never commit anything from the repos, only
  aggregate metrics.
- Restrict analysis strictly to GFC and JNC.
- No secrets. No third-party data exfiltration.
- Stdlib-only Python (no pip installs).

---

## 6. Running It

```bash
cd /nfs/site/disks/nsaddaga_wa/Managing/perfH12026
python3 dashboard_server.py     # defaults above; override with env vars
# then open http://<hostname>:8765/  (default port)
```

## 7. Success Criteria

- Dashboard loads at `http://<host>:8765/`.
- Changing the project drop-down or clicking Refresh visibly re-runs
  `git log` (server logs a new request) and updates every chart, table,
  and KPI within a few seconds.
- All UI text — including chart axis ticks and legends — is ≥12px.
- Overall look is on par with a professional analytics dashboard
  (Grafana/Linear/Tableau feel), not a stock demo.
