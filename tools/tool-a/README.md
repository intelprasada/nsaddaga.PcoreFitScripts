# tool-a

## Overview
`tool-a` is a Python-based utility for demonstrating the repository scaffold.

## Usage
```bash
# Via the bin wrapper (once bin/ is on your PATH):
tool-a [OPTIONS] <input>

# Or directly:
python tools/tool-a/tool_a.py [OPTIONS] <input>
```

## Options
| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help message and exit |
| `-v`, `--verbose` | Enable verbose output |
| `--config FILE` | Path to a YAML config file (default: `configs/defaults.yaml`) |

## Examples
```bash
tool-a hello
tool-a --verbose hello
```

## Dependencies
Install Python dependencies with:
```bash
pip install -r tools/tool-a/requirements.txt
```

## Tests
```bash
python -m pytest tools/tool-a/tests/ -v
# or via Makefile:
make test-tool-a
```
