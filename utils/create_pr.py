#!/usr/bin/env python3
"""
create_pr.py — Create a GitHub pull request via the gh CLI.

Usage:
    python3 create_pr.py --repo owner/repo --head my-feature --title "My PR"
    python3 create_pr.py --repo owner/repo --head my-feature --title "My PR" --body "Details" --base main
"""

import argparse
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a GitHub pull request via the gh CLI."
    )
    parser.add_argument(
        "--repo", required=True, metavar="OWNER/REPO",
        help="GitHub repository, e.g. intelprasada/nsaddaga.PcoreFitScripts"
    )
    parser.add_argument(
        "--head", required=True,
        help="Source branch name (the branch with your changes)"
    )
    parser.add_argument(
        "--title", required=True,
        help="Pull request title"
    )
    parser.add_argument(
        "--base", default="main",
        help="Target branch to merge into (default: main)"
    )
    parser.add_argument(
        "--body", default="",
        help="Pull request description body"
    )
    parser.add_argument(
        "--draft", action="store_true",
        help="Open the PR as a draft"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    cmd = [
        "gh", "pr", "create",
        "--repo", args.repo,
        "--head", args.head,
        "--base", args.base,
        "--title", args.title,
    ]
    if args.body:
        cmd += ["--body", args.body]
    else:
        cmd += ["--body", ""]
    if args.draft:
        cmd += ["--draft"]

    print(f"Creating PR: '{args.title}'")
    print(f"  {args.head} -> {args.base} on {args.repo}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    pr_url = result.stdout.strip().splitlines()[-1]
    print(f"\nPR created: {pr_url}")


if __name__ == "__main__":
    main()
