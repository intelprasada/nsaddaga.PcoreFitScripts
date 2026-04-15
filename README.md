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
