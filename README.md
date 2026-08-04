# core-tools

A curated collection of lightweight command-line tools for the PcoreFit pipeline.

---

## Quickstart

```bash
# Clone and navigate
git clone https://github.com/intelprasada/nsaddaga.PcoreFitScripts.git core-tools
cd core-tools

# Add bin/ to your PATH
export PATH="$PWD/bin:$PATH"

# Install Python dependencies for tool-a
pip install -r tools/tool-a/requirements.txt

# Run a tool
tool-a hello
tool-b hello

# Run all tests
make test
```

---

## Tool Catalog

| Tool | Language | Description |
|------|----------|-------------|
| [tool-a](tools/tool-a/README.md) | Python | Example Python tool demonstrating the scaffold |
| [tool-b](tools/tool-b/README.md) | Perl | Example Perl tool demonstrating the scaffold |
| [email-sender](tools/email-sender/README.md) | Python | Tkinter GUI for composing and sending emails with optional file attachments |
| [supercsv](tools/supercsv/README.md) | Python | Tkinter-based CSV viewer with filtering, sorting, theming, and email export |
| [supertracker](tools/supertracker/README.md) | Python | Tkinter viewer for CTE tracker `.elog` files with repeated header stripping |
| [interfacespec](tools/interfacespec/README.md) | Python | RTL connectivity pipeline + GUI for generating Interface Spec documents from ICF/hier/gen files |
| [gen-smt-todos](tools/gen-smt-todos/README.md) | Python | Scans fe/msid RTL for SMT/JNC TODO comments and emits a TSV summary |
| [teamhub](tools/teamhub/README.md) | Python | Live team-performance dashboard (HTTP server + Chart.js UI) summarizing turnins, commits, HSDs, and pipe-time across GFC and JNC |
| [ValTrak](tools/valtrak/README.md) | Python | Secure vManager validation-plan dashboard with readiness metrics, live refresh, and guarded status updates |

---

## Repository Layout

```
core-tools/
├── README.md          # This file
├── CHANGELOG.md       # Versioned release history
├── VERSION            # Current version (e.g. 1.0.0)
├── Makefile           # Build / test / release targets
├── .gitignore
│
├── bin/               # Thin entry-point wrappers (add to PATH via aliases)
│   ├── interfacespec  # alias: is
│   ├── supercsv       # alias: sc
│   ├── supertracker   # alias: st
│   ├── teamhub        # alias: th
│   └── email-sender   # alias: email
│
├── tools/             # Each tool in its own subdirectory
│   ├── interfacespec/ # RTL connectivity pipeline + GUI
│   │   ├── README.md
│   │   ├── requirements.txt
│   │   └── tests/
│   ├── supercsv/      # CSV viewer/editor GUI
│   │   ├── README.md
│   │   ├── requirements.txt
│   │   └── tests/
│   ├── supertracker/  # Issue tracker GUI
│   │   ├── README.md
│   │   ├── requirements.txt
│   │   └── tests/
│   └── email-sender/  # Email automation tool
│       ├── README.md
│       ├── requirements.txt
│       └── tests/
│   ├── teamhub/       # Live team-performance dashboard (HTTP server + Chart.js UI)
│   │   ├── README.md
│   │   ├── requirements.txt
│   │   ├── dashboard_server.py
│   │   ├── dashboard.html
│   │   ├── gen_prose_summaries.py
│   │   └── tests/
│
├── utils/             # GitHub API utilities + user setup scripts
│   ├── README.md              # Usage documentation
│   ├── setup.sh               # User setup: clone repo + register aliases
│   ├── aliases.sh             # Bash/zsh alias definitions (sourced by setup.sh)
│   ├── aliases.csh            # csh/tcsh alias definitions (sourced by setup.sh)
│   ├── utils.py               # Shared helpers
│   ├── create_remote_branch.py # Step 1: create branch via REST API
│   ├── push_commits.py        # Step 2: push commits via GraphQL
│   ├── create_pr.py           # Step 3: open pull request
│   └── workflow.py            # All-in-one: steps 1–3
│
├── lib/               # Shared libraries
│   ├── python/common_utils.py
│   ├── perl/CommonUtils.pm
│   └── shell/common.sh
│
├── configs/           # Configuration templates
│   └── defaults.yaml
│
├── docs/              # Extended documentation
│   ├── developer-guide.md
│   └── release-process.md
│
├── release/           # Release automation
│   ├── build.sh
│   └── deploy.sh
│
└── tests/             # Repo-wide integration tests
    └── test_integration.sh
```

---

## Make Targets

| Target | Description |
|--------|-------------|
| `make test` | Run all unit and integration tests |
| `make test-tool-a` | Run tool-a unit tests only |
| `make test-tool-b` | Run tool-b unit tests only |
| `make test-integration` | Run integration tests only |
| `make lint` | Lint Python and shell sources |
| `make release` | Build a release tarball |
| `make deploy` | Deploy the release tarball |
| `make clean` | Remove generated artifacts |

---

## Adding a New Tool

See [docs/developer-guide.md](docs/developer-guide.md).

## Release Process

See [docs/release-process.md](docs/release-process.md).

---

## Dev vs Prod branches

This repo uses two long-lived branches:

| Branch | Purpose | Protection |
| --- | --- | --- |
| `main` | Active development. All PRs land here. | No force-push, no deletion. |
| `prod` | Stable code that deployment pipelines pull from. Only fast-forward promotions from `main`. | PR required, no force-push, no deletion. |

### Normal dev flow

1. Branch off `main`.
2. Open a PR into `main`.
3. Merge when checks pass.

### Promoting to prod

Use the **Promote main → prod** workflow (Actions tab → *Promote main → prod* → *Run workflow*).

- Leave `sha` empty to promote the current `main` tip, or enter a specific SHA.
- The workflow refuses any SHA that isn't an ancestor of `main`, and refuses non-fast-forward moves of `prod`.
- Each promotion is tagged `prod-YYYYMMDD-HHMMSS`.

### Hotfix flow

1. Branch off `prod` (e.g. `hotfix/foo`).
2. Open a PR into `prod` and merge.
3. Cherry-pick the fix onto `main` so it doesn't get lost on the next promotion.

### CI conventions

Deployment jobs should gate on `if: github.ref == 'refs/heads/prod'`. All other CI (tests, lint) should run on every branch.

### Running a local prod instance

The `scripts/prod-start.sh` helper runs backend + frontend from a sibling git worktree pinned to `prod`. It does **not** touch the dev processes.

Bootstrap once:

```bash
# From the core-tools (dev) checkout:
git worktree add ../core-tools-prod prod
./VegaNotes/scripts/prod-start.sh --install   # creates prod venv + npm install + vite build
```

Everyday flow:

```bash
./VegaNotes/scripts/prod-start.sh --sync      # ff-only pull to origin/prod
./VegaNotes/scripts/prod-start.sh --restart   # relaunch prod backend + frontend
```

Ports (prod owns the shared URL; dev moved to the high ports):

| | Dev | Prod |
| --- | --- | --- |
| Backend | 8100 | 8000 |
| Frontend | 4173 | 5173 |
| Data dir | `.devdata/` | `.proddata/` |

Frontend runs `vite preview` off a built `dist/` (production bundle), not the dev server.
