# utils

A set of Python scripts to create branches, push commits, and open pull requests
**via the GitHub API** — no `git push` required.

> **Why?** Intel's Fortinet proxy performs SSL inspection and blocks
> `git push` over HTTPS (git smart-HTTP). These scripts use `gh api` and
> `gh api graphql`, which route through the proxy successfully.

---

## Prerequisites

| Requirement | Check |
|---|---|
| `gh` CLI installed | `gh --version` |
| Authenticated to GitHub | `gh auth status` |
| `python3` in PATH | `python3 --version` |
| Repo permissions | `gh api repos/OWNER/REPO --jq '.permissions'` → must include `"push":true` |

Authenticate once with:
```bash
gh auth login
```

---

## Scripts

| Script | Purpose |
|---|---|
| `create_remote_branch.py` | Create a new branch on the remote via the REST API |
| `push_commits.py` | Push local commits to a remote branch via GraphQL |
| `create_pr.py` | Open a pull request via `gh pr create` |
| `workflow.py` | **All three steps in one command** |
| `utils.py` | Shared helpers (not run directly) |

---

## Usage

### Option A — Full workflow (recommended)

Runs all three steps automatically:

```bash
cd /path/to/your/repo

python3 /path/to/utils/workflow.py \
  --repo owner/repo \
  --branch feature/my-feature \
  --title "My feature PR" \
  --body "What this PR does"
```

**All options:**

```
--repo      OWNER/REPO      GitHub repository (required)
--branch    BRANCH          Feature branch name to create (required)
--title     TEXT            Pull request title (required)
--base      BRANCH          Target branch for the PR (default: main)
--body      TEXT            PR description body
--from      REF             Local base ref — commits after this are pushed (default: origin/main)
--to        REF             Local top commit to push (default: HEAD)
--repo-dir  PATH            Path to local git repo (default: current directory)
--base-sha  SHA             Full SHA to branch from (default: tip of origin/main)
--draft                     Open PR as a draft
```

---

### Option B — Run steps individually

#### Step 1 — Create the remote branch

```bash
python3 utils/create_remote_branch.py \
  --repo owner/repo \
  --branch feature/my-feature
```

Optionally specify an explicit SHA to branch from:

```bash
python3 utils/create_remote_branch.py \
  --repo owner/repo \
  --branch feature/my-feature \
  --base-sha abc123def456...
```

---

#### Step 2 — Push local commits

Pushes all commits between `origin/main` and `HEAD` to the remote branch:

```bash
python3 utils/push_commits.py \
  --repo owner/repo \
  --branch feature/my-feature
```

Push a custom range of commits:

```bash
python3 utils/push_commits.py \
  --repo owner/repo \
  --branch feature/my-feature \
  --from origin/main \
  --to HEAD
```

If your repo is not the current directory:

```bash
python3 utils/push_commits.py \
  --repo owner/repo \
  --branch feature/my-feature \
  --repo-dir /path/to/local/repo
```

---

#### Step 3 — Create the pull request

```bash
python3 utils/create_pr.py \
  --repo owner/repo \
  --head feature/my-feature \
  --title "My feature PR" \
  --body "What this PR does"
```

Open as a draft:

```bash
python3 utils/create_pr.py \
  --repo owner/repo \
  --head feature/my-feature \
  --title "My feature PR" \
  --draft
```

---

## Example — Recreating this session's PR

```bash
cd /nfs/site/disks/nsaddaga_wa/MyTools/core-tools

python3 utils/workflow.py \
  --repo intelprasada/nsaddaga.PcoreFitScripts \
  --branch feature/add-new-tools-python3-update \
  --title "Add supercsv, supertracker, interfacespec, email tools & python3 migration" \
  --body "- New tools: supercsv, supertracker, interfacespec, email-sender, gen-smt-todos
- Python3 migration: updated all bin/ executables"
```

---

## How it works

```
git push (blocked by Fortinet)          GitHub API (allowed)
─────────────────────────────           ─────────────────────────────────────
git push origin feature-branch   ✗      Step 1: POST /repos/.../git/refs     ✓
                                         Step 2: GraphQL createCommitOnBranch ✓
                                         Step 3: gh pr create                 ✓
```

- **Step 1** uses the REST API to create a branch ref pointing to the base SHA.
- **Step 2** uses the `createCommitOnBranch` GraphQL mutation to replay each
  local commit onto the remote branch. File contents are base64-encoded and sent
  in the API payload — no git objects need to be transferred over the blocked port.
- **Step 3** uses `gh pr create` to open the PR.
