"""
Re-export shim — authoritative source lives in scripts/supercsv/font_manager.py.

Any import of ``qtgui.font_manager.FontManager`` is transparently forwarded
to the single canonical copy in the supercsv package.
"""

import sys
import os as _os

# Resolve scripts/supercsv/ relative to this file
# __file__ = scripts/interfacespec/qtgui/font_manager.py
# ../../     = scripts/
_sc = _os.path.normpath(
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "supercsv")
)
if _sc not in sys.path:
    sys.path.insert(0, _sc)

# 'font_manager' now resolves to scripts/supercsv/font_manager.py
from font_manager import FontManager  # noqa: E402, F401

__all__ = ["FontManager"]
