"""Shared helpers for utils scripts."""

import json
import subprocess
import sys


def run(cmd, cwd=None):
    """Run a shell command and return stdout as a string. Exits on failure."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"ERROR running: {cmd}\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def gh_api(endpoint, method="GET", fields=None, jq=None):
    """Call the GitHub REST API via gh CLI. Returns parsed JSON."""
    cmd = ["gh", "api", endpoint, "-X", method]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    if jq:
        cmd += ["--jq", jq]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: gh api {endpoint}\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    if jq:
        return result.stdout.strip()
    return json.loads(result.stdout)


def gh_graphql(mutation, variables):
    """Execute a GitHub GraphQL mutation via gh CLI. Returns parsed JSON response."""
    payload = json.dumps({"query": mutation, "variables": variables})
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=payload.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"ERROR: gh graphql mutation failed\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    resp = json.loads(result.stdout)
    if "errors" in resp:
        print(f"GraphQL error: {resp['errors']}", file=sys.stderr)
        sys.exit(1)
    return resp
