# tool-b

## Overview
`tool-b` is a Perl-based utility for demonstrating the repository scaffold.

## Usage
```bash
# Via the bin wrapper (once bin/ is on your PATH):
tool-b [OPTIONS] <input>

# Or directly:
perl tools/tool-b/tool_b.pl [OPTIONS] <input>
```

## Options
| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help message and exit |
| `-v`, `--verbose` | Enable verbose output |

## Examples
```bash
tool-b hello
tool-b --verbose hello
```

## Tests
```bash
prove tools/tool-b/tests/
# or via Makefile:
make test-tool-b
```
