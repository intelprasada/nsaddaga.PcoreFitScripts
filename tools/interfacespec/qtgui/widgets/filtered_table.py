"""
Re-export shim — authoritative source lives in scripts/supercsv/filtered_table.py.

All imports of ``qtgui.widgets.filtered_table.*`` are transparently forwarded
to the single canonical copy in the supercsv package.

Why no circular import:
  This shim is registered by Python as 'qtgui.widgets.filtered_table'.
  The bare 'from filtered_table import ...' below resolves a *top-level*
  module named 'filtered_table' from sys.path — that is a completely
  different entry in sys.modules, so there is no self-import cycle.
"""

import sys
import os as _os

# Resolve scripts/supercsv/ relative to this file
# __file__ = scripts/interfacespec/qtgui/widgets/filtered_table.py
# ../../../   = scripts/
_sc = _os.path.normpath(
    _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "supercsv"
    )
)
if _sc not in sys.path:
    sys.path.insert(0, _sc)

# 'filtered_table' resolves to scripts/supercsv/filtered_table.py
from filtered_table import (  # noqa: E402, F401
    FilteredTable,
    ColumnPickerDialog,
    EmailDialog,
    _apply_col_filter,
    _looks_like_path,
    _resolve_path,
    _open_file,
)

__all__ = [
    "FilteredTable",
    "ColumnPickerDialog",
    "EmailDialog",
    "_apply_col_filter",
    "_looks_like_path",
    "_resolve_path",
    "_open_file",
]
