"""common_utils.py – Shared Python utilities for core-tools."""

import yaml
import sys


def load_config(path: str) -> dict:
    """Load a YAML configuration file and return it as a dict."""
    with open(path, 'r') as fh:
        return yaml.safe_load(fh) or {}


def log(message: str) -> None:
    """Print a log message to stdout."""
    print(message, flush=True)
