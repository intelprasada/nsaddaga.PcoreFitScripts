#!/usr/bin/env python3
"""
create_remote_branch.py — Create a remote branch via the GitHub REST API.

Works behind corporate proxies that block git smart-HTTP push by using
the GitHub API instead of 'git push'.

Usage:
    python3 create_remote_branch.py --repo owner/repo --branch my-feature
    python3 create_remote_branch.py --repo owner/repo --branch my-feature --base-sha <sha>
"""

import argparse
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from utils import gh_api, run


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a remote GitHub branch via the API (no git push required)."
    )
    parser.add_argument(
        "--repo", required=True, metavar="OWNER/REPO",
        help="GitHub repository, e.g. intelprasada/nsaddaga.PcoreFitScripts"
    )
    parser.add_argument(
        "--branch", required=True,
        help="Name of the new branch to create, e.g. feature/my-feature"
    )
    parser.add_argument(
        "--base-sha", default=None,
        help="Full SHA to branch from (default: tip of origin/main in current repo)"
    )
    parser.add_argument(
        "--repo-dir", default=".",
        help="Path to the local git repo (used to resolve base SHA when --base-sha is omitted)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    base_sha = args.base_sha
    if not base_sha:
        base_sha = run("git rev-parse origin/main", cwd=args.repo_dir)
        print(f"Resolved base SHA from origin/main: {base_sha}")

    print(f"Creating branch '{args.branch}' on {args.repo} at {base_sha[:12]}...")
    resp = gh_api(
        f"repos/{args.repo}/git/refs",
        method="POST",
        fields={"ref": f"refs/heads/{args.branch}", "sha": base_sha},
    )
    print(f"Created: {resp['ref']}")
    print(f"SHA:     {resp['object']['sha']}")


if __name__ == "__main__":
    main()
