#!/usr/bin/env python3
"""
workflow.py — Full end-to-end workflow: create remote branch, push commits, open PR.

This combines all three steps into a single command. Useful when git push is blocked
by a corporate proxy (e.g. Fortinet SSL inspection on Intel networks).

Usage:
    python3 workflow.py --repo owner/repo --branch my-feature --title "My PR"
    python3 workflow.py --repo owner/repo --branch my-feature --title "My PR" \\
                        --body "Details" --base main --repo-dir /path/to/repo
"""

import argparse
import subprocess
import sys
import os

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import create_remote_branch
import push_commits
import create_pr


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Full workflow: create remote branch + push commits + open PR "
            "(uses GitHub API, no git push required)."
        )
    )
    parser.add_argument(
        "--repo", required=True, metavar="OWNER/REPO",
        help="GitHub repository, e.g. intelprasada/nsaddaga.PcoreFitScripts"
    )
    parser.add_argument(
        "--branch", required=True,
        help="Feature branch name to create, e.g. feature/my-feature"
    )
    parser.add_argument(
        "--title", required=True,
        help="Pull request title"
    )
    parser.add_argument(
        "--base", default="main",
        help="Target branch for the PR (default: main)"
    )
    parser.add_argument(
        "--body", default="",
        help="Pull request description body"
    )
    parser.add_argument(
        "--from", dest="base_ref", default="origin/main",
        help="Local base ref: commits after this point are pushed (default: origin/main)"
    )
    parser.add_argument(
        "--to", dest="head_ref", default="HEAD",
        help="Top local commit to push (default: HEAD)"
    )
    parser.add_argument(
        "--repo-dir", default=".",
        help="Path to the local git repository (default: current directory)"
    )
    parser.add_argument(
        "--draft", action="store_true",
        help="Open the PR as a draft"
    )
    parser.add_argument(
        "--base-sha", default=None,
        help="Full SHA to branch from (default: tip of origin/main)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("STEP 1: Create remote branch")
    print("=" * 60)
    branch_args = argparse.Namespace(
        repo=args.repo,
        branch=args.branch,
        base_sha=args.base_sha,
        repo_dir=args.repo_dir,
    )
    create_remote_branch.main.__globals__["sys"].argv = []
    # Call directly with namespace
    _run_step(create_remote_branch, branch_args)

    print()
    print("=" * 60)
    print("STEP 2: Push commits via GraphQL API")
    print("=" * 60)
    push_args = argparse.Namespace(
        repo=args.repo,
        branch=args.branch,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        repo_dir=args.repo_dir,
    )
    _run_step(push_commits, push_args)

    print()
    print("=" * 60)
    print("STEP 3: Create pull request")
    print("=" * 60)
    pr_args = argparse.Namespace(
        repo=args.repo,
        head=args.branch,
        base=args.base,
        title=args.title,
        body=args.body,
        draft=args.draft,
    )
    _run_step(create_pr, pr_args)

    print()
    print("Workflow complete!")


def _run_step(module, ns):
    """Invoke a module's main() with a pre-built Namespace, bypassing argparse."""
    original_parse = module.parse_args
    module.parse_args = lambda: ns
    try:
        module.main()
    finally:
        module.parse_args = original_parse


if __name__ == "__main__":
    main()
