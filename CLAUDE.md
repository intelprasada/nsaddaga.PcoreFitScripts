# CLAUDE.md — Agent operating rules for this repo

**Read this file first, every session, before making any changes.** It governs how AI agents (Claude, Copilot, etc.) must operate inside this repository.

---

## 1. Which branch / worktree am I in?

Run first, always:

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse --show-toplevel
```

Map the answer to a role:

| Toplevel path ends with… | Role | HEAD is usually |
| --- | --- | --- |
| `…/core-tools`        | **DEV**  | `main`, or a feature branch off `main` |
| `…/core-tools-prod`   | **PROD** | `prod` (a git worktree pinned to it) |

If the toplevel is unexpected or `HEAD` doesn't match the table, **stop and ask** — do not guess.

---

## 2. Golden rules (never violate)

1. **Never commit directly to `main` or `prod`.** Always branch off, PR in.
2. **Never push to `prod` from a local shell.** Prod only moves via the `Promote main → prod` GitHub Actions workflow. If you need code in prod, get it into `main` first and then promote.
3. **Never force-push `main` or `prod`.** Both are branch-protected; a force-push attempt is a bug, not a workaround.
4. **Never delete `main` or `prod`.**
5. **Never edit files inside `core-tools-prod/` and commit them from there.** The prod worktree is read-only in spirit — code changes happen in `core-tools/` (dev) and reach prod only via promotion.
6. **Never mix dev and prod data.** `.devdata/` and `.proddata/` are separate on purpose. Do not point one at the other, do not copy DB files between them without explicit user request.
7. **Never touch running processes on the "other" side.** If you are in dev, do not kill / restart the prod backend/frontend and vice versa. Ports are how you tell them apart:

   | | Backend | Frontend | Data dir |
   | --- | --- | --- | --- |
   | Dev  | `:8100` | `:4173` (vite dev) | `.devdata/` |
   | Prod | `:8000` | `:5173` (vite preview) | `.proddata/` |

---

## 3. Dev flow (when in `core-tools/`)

- Branch off `origin/main`, prefix `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- Make changes, run tests / lint / build for the affected sub-project.
- Push (see §7 for the corp-proxy push workaround), open a PR into `main`.
- Merge only after checks are green.

Managing the local dev server:

```bash
./VegaNotes/scripts/dev-start.sh              # start both
./VegaNotes/scripts/dev-start.sh --restart    # relaunch
```

---

## 4. Prod flow (when in `core-tools-prod/`)

You are in a **read-mostly, run-and-inspect** worktree. Acceptable actions:

- Read code / logs to answer questions.
- Run the prod app (`prod-start.sh --sync`, `--restart`, etc.).
- Reproduce a prod-only bug.
- Inspect the `.proddata/` DB.

**Not acceptable without explicit user approval:**

- Any `git commit` in this worktree.
- Any file edit that isn't a temporary debugging aid.
- Any manual `git push` to `prod`.

If a bug must be fixed, hop back to `core-tools/`:

```bash
cd /nfs/site/disks/nsaddaga_wa/FitScripts/core-tools
git checkout -b fix/<slug> origin/main    # normal dev flow
```

For a **hotfix** that must land on `prod` before `main` catches up:

1. In `core-tools/`: `git checkout -b hotfix/<slug> origin/prod`.
2. Commit the fix, push, open PR into `prod`.
3. After merge, cherry-pick to `main` so the fix isn't lost on the next promotion.

---

## 5. Promoting main → prod

Only method: GitHub Actions → **Promote main → prod** → *Run workflow*.

- Leave SHA blank to promote current `main` tip, or enter a specific SHA.
- Workflow refuses non-ancestor SHAs and non-fast-forward moves.
- Every promotion is tagged `prod-YYYYMMDD-HHMMSS`.

After promotion, on the local prod worktree:

```bash
./VegaNotes/scripts/prod-start.sh --sync      # ff to origin/prod
./VegaNotes/scripts/prod-start.sh --install   # only if package.json / requirements changed
./VegaNotes/scripts/prod-start.sh --restart   # relaunch
```

---

## 6. What NOT to run in prod worktree

- `git commit` (unless the user explicitly asks and understands the PR-into-prod flow).
- `git push origin prod` from a laptop shell (protected + convention says no).
- `rm -rf .proddata/` without a full backup and user's explicit go-ahead.
- Any `--force` git flag on `main` or `prod`.
- Ad-hoc `pip install` / `npm install` that mutates the running venv/node_modules without going through `prod-start.sh --install` (keeps the "one way in" property).

---

## 7. Landing changes in `main` when corp proxy blocks `git push`

`git-receive-pack` is blocked by the corp proxy (HTTP 403, EC/HPC Policy
241). Do **not** retry `git push` in a loop; it will not succeed from this
environment.

The workaround still follows the normal protected-branch flow:

```text
local feature branch
  → GitHub feature branch (Git Data REST API)
  → issue + PR targeting main
  → required checks
  → squash merge
  → main
```

Never use this workaround to create or update `main` or `prod` directly.

### 7.1 Prepare and commit a branch

Use a fresh worktree based on the latest `origin/main`. This avoids changing
the branch in another session's worktree:

```bash
cd /nfs/site/disks/nsaddaga_wa/FitScripts/core-tools
git fetch origin main
git worktree add /tmp/ct-<slug> -b <type>/<slug> origin/main
cd /tmp/ct-<slug>

# edit, test, and review the diff
git add <files>
git commit -m "<type>(<scope>): <summary>

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

The REST script pushes committed `HEAD`, not uncommitted working-tree changes.

### 7.2 Publish the feature branch through the Git Data API

Duplicate one of the existing `/tmp/push_*.py` templates rather than modifying
a template that another session may be using:

```bash
cp /tmp/push_320_dsl.py /tmp/push_<slug>.py
```

Set these constants at the top of the copy:

```python
NEW_BRANCH = "<type>/<slug>"
BASE_BRANCH = "main"
```

The script should:

1. Resolve `origin/main` and its tree.
2. Upload each changed file with `POST /repos/{owner}/{repo}/git/blobs`.
3. Create a tree based on the `origin/main` tree with
   `POST /repos/{owner}/{repo}/git/trees`.
4. Create a commit with the local commit message and `origin/main` as its
   parent using `POST /repos/{owner}/{repo}/git/commits`.
5. Create `refs/heads/<type>/<slug>` with
   `POST /repos/{owner}/{repo}/git/refs`.

Run it from the feature worktree so `git rev-parse HEAD` and the diff resolve
to the intended branch:

```bash
cd /tmp/ct-<slug>
export https_proxy=http://proxy-dmz.intel.com:912
export http_proxy=http://proxy-dmz.intel.com:911
export no_proxy=localhost,127.0.0.1,.intel.com
GITHUB_TOKEN=$(gh auth token) python3 /tmp/push_<slug>.py
```

Confirm that the script created `refs/heads/<type>/<slug>`. If the ref already
exists, stop and inspect it; do not force-update it or silently overwrite work
from another session.

### 7.3 Create the issue and PR

Use body files so shell quoting, Markdown, and command names are preserved:

```bash
gh issue create \
  --repo intelprasada/nsaddaga.PcoreFitScripts \
  --title "<issue title>" \
  --body-file /tmp/<slug>-issue.md

gh pr create \
  --repo intelprasada/nsaddaga.PcoreFitScripts \
  --base main \
  --head <type>/<slug> \
  --title "<PR title>" \
  --body-file /tmp/<slug>-pr.md
```

Include `Closes #<issue-number>.` in the PR body. Do not add a label unless
the label is known to exist; `gh issue create` fails if a requested label is
invalid.

### 7.4 Wait for checks and merge

```bash
gh pr checks <pr-number> \
  --repo intelprasada/nsaddaga.PcoreFitScripts

gh pr merge <pr-number> \
  --repo intelprasada/nsaddaga.PcoreFitScripts \
  --squash \
  --delete-branch

gh pr view <pr-number> \
  --repo intelprasada/nsaddaga.PcoreFitScripts \
  --json state,mergedAt
```

Merge only after all required checks pass. `--squash` lands the reviewed PR
in `main`; this is the only step that updates `main`.

### 7.5 Clean up

After confirming the PR state is `MERGED`:

```bash
cd /nfs/site/disks/nsaddaga_wa/FitScripts/core-tools
git worktree remove /tmp/ct-<slug> --force
rm -f /tmp/push_<slug>.py /tmp/<slug>-issue.md /tmp/<slug>-pr.md
```

Delete only the named temporary files and worktree created for the task.

---

## 8. Test / build gates before opening a PR

Backend:

```bash
cd VegaNotes/backend && .venv/bin/pytest \
  --deselect tests/test_watcher.py::test_polling_awatch_observes_external_change \
  --deselect tests/parser/test_parser.py::test_golden \
  --deselect tests/api/test_api.py::test_create_and_query
```

Frontend:

```bash
cd VegaNotes/frontend && npm run test -- --run && npm run tsc && npm run build
```

Do not add new deselects without justification.

---

## 9. When in doubt

Ask the user. Anything about promotion, force-push, data-dir mutation, or cross-branch cherry-pick is a policy decision, not an agent decision.
