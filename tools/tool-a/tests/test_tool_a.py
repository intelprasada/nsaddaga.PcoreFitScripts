"""Tests for tool_a.py."""

import sys
import os
import pytest

# Ensure the tool and lib are importable
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools', 'tool-a'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'lib', 'python'))

from tool_a import parse_args, run  # noqa: E402


def test_parse_args_defaults():
    args = parse_args(['hello'])
    assert args.input == 'hello'
    assert args.verbose is False


def test_parse_args_verbose():
    args = parse_args(['-v', 'hello'])
    assert args.verbose is True


def test_run_uppercases_input(tmp_path):
    config_file = tmp_path / 'defaults.yaml'
    config_file.write_text('tool_a:\n  setting: test\n')
    args = parse_args(['--config', str(config_file), 'hello'])
    result = run(args)
    assert result == 'HELLO'


def test_run_with_verbose(tmp_path, capsys):
    config_file = tmp_path / 'defaults.yaml'
    config_file.write_text('tool_a:\n  setting: test\n')
    args = parse_args(['--config', str(config_file), '-v', 'world'])
    result = run(args)
    assert result == 'WORLD'
