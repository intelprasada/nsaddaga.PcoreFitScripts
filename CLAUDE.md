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

## 7. Push workaround (corp proxy blocks `git push`)

`git-receive-pack` is blocked by corp proxy (HTTP 403, EC/HPC Policy 241). Do NOT retry `git push` in a loop — it will never succeed.

Use the REST-API push script template. There is one on disk you can duplicate:

```bash
cp /tmp/push_320_dsl.py /tmp/push_<slug>.py
# edit NEW_BRANCH and BASE_BRANCH at the top
GITHUB_TOKEN=$(gh auth token) python3 /tmp/push_<slug>.py
```

The script uploads blobs → tree → commit → ref via the GitHub Git Data API. It requires:

- `git fetch origin <BASE_BRANCH>` first, so `origin/<BASE_BRANCH>` resolves.
- A committed HEAD (script pushes HEAD, not the working tree).

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
