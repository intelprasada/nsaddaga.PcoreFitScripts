---
name: codelens
description: >
  Launch CodeLens, an IDE-like web browser for the GFC / JNC front-end
  (core/fe). Reads code in a left pane and shows live per-visible-line context
  on the right: git-blame authorship (with resolved IDSIDs), the turnins that
  introduced those lines, and the HSD ids referenced by the introducing
  commits. Use to review who/why/when for the code currently on screen.
metadata:
  owner: Navadeep Saddaga
  owner_linux_name: nsaddaga
  ai_note: Written with assistance from Claude Opus 4.8
---

## Scope

Use this skill when asked to:

- Browse `core/fe` RTL / test-bench sources in an IDE-like view.
- See **who last changed** the lines currently on screen (line-level blame).
- Find **which turnin** introduced a block of code.
- Surface the **HSD ids** referenced by the commits behind some lines.
- Correlate authorship / turnins / HSDs for an arbitrary visible line range,
  with a user-configurable pane size (number of lines).

Do **not** use this skill for whole-repo turnin/HSD dashboards across a team —
that is `tools/teamhub`. CodeLens is file- and line-range-centric.

## Prerequisites

- `git` and (for IDSID resolution) `phonebook` on `PATH`.
- Read access to a GFC and/or JNC working tree. By default CodeLens resolves
  the same `-latest` turnin symlinks `setGFC` / `setJNCfit` use; a local clone
  is faster and is supplied via `JNC_REPO` / `GFC_REPO`.
- The machine opening the page needs outbound HTTPS (Monaco loads from a CDN).

## Step-by-step workflow

1. **Start the server** (from the repo root):

   ```bash
   bin/codelens
   # or, pointing at local clones:
   JNC_REPO=/nfs/site/disks/<you>/JNC_2/core \
   GFC_REPO=/nfs/site/disks/<you>/GFC_2/core bin/codelens
   ```

   It prints `http://localhost:8770` and the resolved repo roots.

2. **Open the URL**, pick the repo (top-bar selector), and click a file in the
   left tree. The center pane shows the source; the gutter bar colors each line
   by its last author.

3. **Read the right pane.** It lists, for the lines on screen: authors (+IDSID
   +line counts), turnins, referenced HSDs (linked to HSD-ES), and a per-commit
   breakdown. Scroll and it recomputes automatically.

4. **Set the pane size** in the `pane` box (`auto`, or a line count like `60`)
   to control how many lines the context summarizes. Use `⟳` to force-refresh.

## Scriptable API

For non-UI use, the same data is available as JSON — most usefully:

```bash
curl -s "http://localhost:8770/api/context?repo=JNC&path=rtl/baaddpvd.vs&start=1&end=60"
```

returns `{authors[], turnins[], hsds[], commits[]}` for that range. See
`tools/codelens/README.md` for the full endpoint list and environment knobs.

## Notes & limitations

- HSD ids are shown and linked but not yet resolved to title/status/owner;
  spec linking and AI-review comment surfacing are planned follow-ups.
- Per-sha enrichment is cached forever under `tools/codelens/.cache/`
  (git history is immutable); the IDSID cache is `.idsid_cache.json`.
