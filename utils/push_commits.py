#!/usr/bin/env python3
"""
push_commits.py — Push local commits to a remote branch via the GitHub GraphQL API.

Uses the 'createCommitOnBranch' mutation, which works through corporate proxies
that block git smart-HTTP push (e.g. Fortinet SSL inspection).

Usage:
    python3 push_commits.py --repo owner/repo --branch my-feature
    python3 push_commits.py --repo owner/repo --branch my-feature --from origin/main --to HEAD
"""

import argparse
import base64
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from utils import gh_graphql, run

CREATE_COMMIT_MUTATION = """
mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit { oid url }
  }
}
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Push local commits to a remote branch via the GitHub GraphQL API."
    )
    parser.add_argument(
        "--repo", required=True, metavar="OWNER/REPO",
        help="GitHub repository, e.g. intelprasada/nsaddaga.PcoreFitScripts"
    )
    parser.add_argument(
        "--branch", required=True,
        help="Name of the remote branch to push to"
    )
    parser.add_argument(
        "--from", dest="base_ref", default="origin/main",
        help="Base ref: commits after this are pushed (default: origin/main)"
    )
    parser.add_argument(
        "--to", dest="head_ref", default="HEAD",
        help="Top commit ref to push up to (default: HEAD)"
    )
    parser.add_argument(
        "--repo-dir", default=".",
        help="Path to the local git repository (default: current directory)"
    )
    return parser.parse_args()


def get_commits(base_ref, head_ref, repo_dir):
    """Return list of (full_sha, message) tuples from oldest to newest."""
    out = run(
        f"git log --reverse --format='%H %s' {base_ref}..{head_ref}",
        cwd=repo_dir,
    )
    if not out:
        print("No commits found between the specified refs.", file=sys.stderr)
        sys.exit(0)
    commits = []
    for line in out.splitlines():
        sha, _, msg = line.partition(" ")
        commits.append((sha.strip(), msg.strip()))
    return commits


def get_changed_files(sha, parent_sha, repo_dir):
    """Return (additions list, deletions list) for a single commit."""
    diff = run(
        f"git diff --name-status {parent_sha}..{sha}",
        cwd=repo_dir,
    )
    adds, dels = [], []
    for line in diff.splitlines():
        if not line:
            continue
        parts = line.split("\t", 1)
        status, path = parts[0][0], parts[1]
        if status in ("A", "M"):
            adds.append(path)
        elif status == "D":
            dels.append(path)
    return adds, dels


def get_file_content_b64(sha, path, repo_dir):
    raw = subprocess.check_output(
        ["git", "show", f"{sha}:{path}"], cwd=repo_dir
    )
    return base64.b64encode(raw).decode()


def get_remote_head_sha(repo, branch):
    """Get the current HEAD SHA of the remote branch."""
    from utils import gh_api
    resp = gh_api(f"repos/{repo}/git/refs/heads/{branch}")
    return resp["object"]["sha"]


def main():
    args = parse_args()

    commits = get_commits(args.base_ref, args.head_ref, args.repo_dir)
    print(f"Found {len(commits)} commit(s) to push to '{args.branch}'")

    current_head = get_remote_head_sha(args.repo, args.branch)
    print(f"Remote branch HEAD: {current_head[:12]}")

    for sha, msg in commits:
        parent_sha = run(f"git rev-parse {sha}^", cwd=args.repo_dir)
        adds, dels = get_changed_files(sha, parent_sha, args.repo_dir)
        print(f"\n  Commit: {sha[:12]}  \"{msg}\"")
        print(f"    +{len(adds)} file(s)  -{len(dels)} file(s)")

        variables = {
            "input": {
                "branch": {
                    "repositoryNameWithOwner": args.repo,
                    "branchName": args.branch,
                },
                "message": {"headline": msg},
                "fileChanges": {
                    "additions": [
                        {"path": p, "contents": get_file_content_b64(sha, p, args.repo_dir)}
                        for p in adds
                    ],
                    "deletions": [{"path": p} for p in dels],
                },
                "expectedHeadOid": current_head,
            }
        }

        resp = gh_graphql(CREATE_COMMIT_MUTATION, variables)
        commit_data = resp["data"]["createCommitOnBranch"]["commit"]
        current_head = commit_data["oid"]
        print(f"    -> {commit_data['url']}")

    print(f"\nAll commits pushed. Final HEAD: {current_head[:12]}")


if __name__ == "__main__":
    main()
