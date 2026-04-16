"""
TabTooltip — hover tooltip for ttk.Notebook tabs.

Attach once to a Notebook; tooltip text is read from the ``_path``
attribute stored on each tab's child widget (set it to whatever string
you want displayed).  If ``_path`` is absent or empty no tooltip appears.

Usage::

    TabTooltip(notebook)   # no reference needs to be kept

Implementation lives in ``lib/python/tk_widgets.py`` (shared across tools).
"""

from tk_widgets import TabTooltip  # noqa: F401  re-exported for local imports
