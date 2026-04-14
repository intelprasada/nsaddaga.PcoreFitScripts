# core-tools

A curated collection of lightweight command-line tools for the PcoreFit pipeline.

---

## Quickstart

```bash
# Clone and navigate
git clone https://github.com/intelprasada/PcoreFitScriptsSandbox.git core-tools
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
├── bin/               # Thin entry-point wrappers (add to PATH)
│   ├── tool-a
│   └── tool-b
│
├── tools/             # Each tool in its own subdirectory
│   ├── tool-a/
│   │   ├── README.md
│   │   ├── tool_a.py
│   │   ├── requirements.txt
│   │   └── tests/test_tool_a.py
│   └── tool-b/
│       ├── README.md
│       ├── tool_b.pl
│       └── tests/test_tool_b.t
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