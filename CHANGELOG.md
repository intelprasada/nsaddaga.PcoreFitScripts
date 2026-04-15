# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **email-sender** — Tkinter GUI for composing and sending emails with optional
  file attachments; uses Python stdlib only (smtplib, tkinter).
- **supercsv** — Tkinter CSV viewer with column filtering, row sorting, font/theme
  management, and integrated email-export dialog.  Requires `pandas>=1.3`.
- **interfacespec** — Seven-stage RTL connectivity pipeline
  (`run_cluster_pipeline.py`) plus a multi-tab Tkinter GUI
  (`interfacespec-gui`) that drives the pipeline and renders Interface Spec
  markdown.  Requires `pandas>=1.3`.
- **supertracker** — Tkinter viewer for CTE tracker `.elog` / `.elog.gz` files;
  strips repeating header blocks before rendering in a FilteredTable.  Depends
  on the `supercsv` shared widget layer (resolved via `PYTHONPATH` in the bin
  wrapper).  Requires `pandas>=1.3`.
  lines containing both a TODO/FIXME marker and an SMT/JNC/Thread-1 keyword;
  emits a TSV report and per-file summary.  Stdlib only.

## [1.0.0] - 2026-04-14

### Added
- Initial repository scaffold with directory layout
- `tool-a`: Python-based example tool
- `tool-b`: Perl-based example tool
- Shared libraries: `lib/python/common_utils.py`, `lib/perl/CommonUtils.pm`, `lib/shell/common.sh`
- Default configuration: `configs/defaults.yaml`
- Documentation: developer guide and release process
- Release automation scripts: `release/build.sh`, `release/deploy.sh`
- Repo-wide integration test: `tests/test_integration.sh`
- `Makefile` with build, test, and release targets

[Unreleased]: https://github.com/intelprasada/PcoreFitScriptsSandbox/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/intelprasada/PcoreFitScriptsSandbox/releases/tag/v1.0.0
