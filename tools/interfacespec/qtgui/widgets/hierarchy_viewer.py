"""
HierarchyViewer — popup window showing the core module hierarchy.

Treeview — lazy-loaded, one level deep at a time.
Right-click any node → "Drill down" to expand its children.

Data source: .hier files resolved via hier_utils (relative to model_root).
"""

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import List

from ..hier_utils import (
    ICORE_HIER_REL,
    build_children_map,
)


def get_children(module: str, model_root: str) -> List[str]:
    """Return filtered direct children of *module*.

    Backward-compatible public helper backed by the cached children_map.
    """
    return build_children_map(model_root).get(module, [])


# ── viewer toplevel ───────────────────────────────────────────────────────────

_DUMMY = "__lazy__"   # placeholder child that triggers lazy load


class HierarchyViewer(tk.Toplevel):
    """
    Popup window: lazy-loaded Treeview of the core module hierarchy.

    • One level shown at a time.
    • Right-click any node → "Drill down" to expand its children.

    Usage::
        HierarchyViewer(parent_widget, model_root="/path/to/model/root")
    """

    def __init__(self, parent, model_root: str):
        super().__init__(parent)
        self.title("Core Module Hierarchy")
        self.geometry("700x560")
        self.minsize(500, 400)
        self._model_root = model_root

        icore_path = Path(model_root) / ICORE_HIER_REL
        if not icore_path.exists():
            messagebox.showerror(
                "Hierarchy",
                f"icore.hier not found at:\n{icore_path}",
                parent=self,
            )
            self.destroy()
            return

        # Pre-build children map (cached in hier_utils) for fast lazy loading.
        # This also correctly handles:
        #   - nested inline module declarations (bac → baddbacons)
        #   - sub-modules in parent cluster's rtl dir (fe/ifu, ooo/rat, …)
        self._children_map = build_children_map(model_root)

        self._build_ui()

    def _kids(self, module: str) -> List[str]:
        """Return direct children of *module* from the precomputed map."""
        return self._children_map.get(module, [])

    def _bare(self, node_iid: str) -> str:
        """Extract the bare module name from a tree iid (e.g. 'fe/ifu' → 'ifu')."""
        return node_iid.rsplit("/", 1)[-1]

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ttk.Label(
            self,
            text="Core Module Hierarchy  —  right-click a node → Drill down",
            font=("Helvetica", 11, "bold"),
        ).pack(side=tk.TOP, pady=(8, 4))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self._build_tree(frame)

    # ── tree view (lazy) ──────────────────────────────────────────────────────

    def _build_tree(self, parent) -> None:
        tv = ttk.Treeview(parent, show="tree headings", columns=("n",))
        tv.heading("#0", text="Module")
        tv.heading("n",  text="#")
        tv.column("#0", width=170, minwidth=110)
        tv.column("n",  width=44, anchor="center")

        vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tv.pack(fill=tk.BOTH, expand=True)

        self._tv = tv

        # Seed icore + its direct children (depth=1)
        root_kids = self._kids("icore")
        root_node = tv.insert(
            "", tk.END, iid="icore",
            text="icore", values=(len(root_kids),), open=True,
        )
        for child in root_kids:
            sub_kids = self._kids(child)
            node = tv.insert(
                root_node, tk.END, iid=child,
                text=child, values=(len(sub_kids) if sub_kids else "—"),
                open=False,
            )
            if sub_kids:
                tv.insert(node, tk.END, iid=f"{child}{_DUMMY}",
                          text="", values=("",))

        tv.bind("<<TreeviewOpen>>", self._on_open)
        tv.bind("<Button-3>",       self._on_right_click)

        # Focus icore on open
        tv.focus("icore")

    def _on_open(self, _event) -> None:
        """Lazy-load children when a node is first opened."""
        node = self._tv.focus()
        if not node:
            return
        children = self._tv.get_children(node)
        if len(children) == 1 and children[0].endswith(_DUMMY):
            self._load_children(node)

    def _load_children(self, node_iid: str) -> None:
        """Remove dummy placeholder and insert real children one level deep."""
        tv = self._tv
        for ch in tv.get_children(node_iid):
            if ch.endswith(_DUMMY):
                tv.delete(ch)

        # Extract bare module name: "fe/ifu" → "ifu", "ooo" → "ooo"
        module_name = self._bare(node_iid)
        kids = self._kids(module_name)
        for child in kids:
            iid = f"{node_iid}/{child}"
            if tv.exists(iid):
                continue
            sub_kids = self._kids(child)
            n = tv.insert(
                node_iid, tk.END, iid=iid,
                text=child, values=(len(sub_kids) if sub_kids else "—"),
                open=False,
            )
            if sub_kids:
                tv.insert(n, tk.END, iid=f"{iid}{_DUMMY}",
                          text="", values=("",))

    def _on_right_click(self, event) -> None:
        """Context menu: Drill down / Collapse."""
        tv  = self._tv
        row = tv.identify_row(event.y)
        if not row:
            return
        tv.selection_set(row)
        tv.focus(row)

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="🔍  Drill down (expand children)",
            command=lambda: self._drill_down(row),
        )
        menu.add_separator()
        menu.add_command(
            label="🔼  Collapse",
            command=lambda: tv.item(row, open=False),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _drill_down(self, node_iid: str) -> None:
        """Expand node and lazy-load its children if not yet loaded."""
        tv = self._tv
        children = tv.get_children(node_iid)
        if len(children) == 1 and children[0].endswith(_DUMMY):
            self._load_children(node_iid)
        tv.item(node_iid, open=True)
