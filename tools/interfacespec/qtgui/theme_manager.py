"""
Re-export shim — authoritative source lives in scripts/supercsv/theme_manager.py.

Any import of ``qtgui.theme_manager.ThemeManager`` is transparently forwarded
to the single canonical copy in the supercsv package.
"""

import sys
import os as _os

# Resolve scripts/supercsv/ relative to this file
# __file__ = scripts/interfacespec/qtgui/theme_manager.py
# ../../     = scripts/
_sc = _os.path.normpath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "supercsv")
)
if _sc not in sys.path:
    sys.path.insert(0, _sc)

# 'theme_manager' now resolves to scripts/supercsv/theme_manager.py
from theme_manager import ThemeManager  # noqa: E402, F401

__all__ = ["ThemeManager"]
