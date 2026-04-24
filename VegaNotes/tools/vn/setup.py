"""Compatibility shim for older pip versions (<21.3) that can't perform a
PEP 660 editable install from pyproject.toml alone. All real metadata lives
in pyproject.toml."""

from setuptools import setup

setup()
