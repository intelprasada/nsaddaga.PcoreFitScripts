"""
hier_utils.py — .hier file parsing utilities and module hierarchy mapping.

Standalone module: no imports from other qtgui modules (avoids circular imports).

Public API
----------
keep_module(name)                          → bool
extract_children(path, top_module)         → List[str]
resolve_hier_path(module, root, cluster_rtl_dir=None) → Optional[Path]
get_children(module, model_root)           → List[str]   (backward-compat)
build_module_parent_map(model_root)        → Dict[str, Optional[str]]
build_children_map(model_root)             → Dict[str, List[str]]
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

# ── Constants ──────────────────────────────────────────────────────────────────

# Path to icore.hier relative to model_root
ICORE_HIER_REL = "core/common/rtl/global/icore.hier"

# Overrides: modules whose .hier is NOT at core/{module}/rtl/{module}.hier
MODULE_HIER_OVERRIDES: Dict[str, str] = {
    "icore_clk": "core/common/rtl/clk/icore_clk.hier",
    "core_fivr":  "core/common/rtl/core_fivr.hier",
}

# Top-level clusters absent from icore.hier — seeded as conceptual icore children
_EXTRA_TOP_LEVEL_CLUSTERS = ("mlc", "pm")

# Noise-module filter rules
_IGNORE_SUFFIXES = ("_inst", "_tlm", "_mon", "_pwr_sig", "_sig_map", "_sva")
_IGNORE_PREFIXES = ("spy_clocks__",)
_IGNORE_EXACT    = frozenset({"bisr_chain_buffer"})
_IGNORE_CONTAINS = ("ijtag_rtl", "rtlsi_mon")

# Per-model_root caches
_parent_map_cache:   Dict[str, Dict[str, Optional[str]]] = {}
_children_map_cache: Dict[str, Dict[str, List[str]]]     = {}


# ── Filter ────────────────────────────────────────────────────────────────────

def keep_module(name: str) -> bool:
    """Return True if the module name should be included in hierarchy output."""
    n = name.lower()
    if n in _IGNORE_EXACT:
        return False
    if any(n.endswith(s) for s in _IGNORE_SUFFIXES):
        return False
    if any(n.startswith(p) for p in _IGNORE_PREFIXES):
        return False
    if any(sub in n for sub in _IGNORE_CONTAINS):
        return False
    return True


# ── .hier file parsing ────────────────────────────────────────────────────────

def extract_children(path: Path, top_module: str) -> List[str]:
    """Parse a .hier file and return first-level child modules under *top_module*.

    Handles two declaration styles:

    1. Simple instantiation at depth=1 inside top_module::

           bac_clks bac_clks;

    2. Inline nested module declaration at depth=1::

           module baddbacons;
               ...
           endmodule
    """
    text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    in_top, depth = False, 0
    result: List[str] = []
    seen: set = set()

    for raw in text:
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue

        # ── module/endmodule depth tracking ───────────────────────────────────
        m = re.match(r"^module\s+([A-Za-z_]\w*)\b", line)
        if m:
            depth += 1
            mod_name = m.group(1)
            if mod_name == top_module and not in_top:
                # Entering the target module scope
                in_top = True
            elif in_top and depth == 2:
                # Inline nested module declaration → treat as a child
                if keep_module(mod_name) and mod_name not in seen:
                    seen.add(mod_name)
                    result.append(mod_name)
            continue

        if re.search(r"\bendmodule\b", line):
            if in_top and depth == 1:
                break                        # exiting top_module scope → done
            depth = max(0, depth - 1)
            continue

        # ── only parse instantiations at depth=1 inside top_module ────────────
        if not in_top or depth != 1:
            continue
        if line.startswith(("`", "(*", "import ")):
            continue

        # Match: <module_type> <instance_name> [optional_array] ;
        im = re.match(r"^([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*(?:\[[^\]]+\])?\s*;", line)
        if not im:
            continue

        child = im.group(1)
        if keep_module(child) and child not in seen:
            seen.add(child)
            result.append(child)

    return result


def resolve_hier_path(
    module: str,
    root: Path,
    cluster_rtl_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the .hier file path for *module*, or None if not found.

    Search order:
    1. ``MODULE_HIER_OVERRIDES``
    2. ``cluster_rtl_dir/{module}.hier``  (if cluster_rtl_dir provided)
    3. ``core/{module}/rtl/{module}.hier``  (standard top-level cluster location)
    """
    if module in MODULE_HIER_OVERRIDES:
        p = root / MODULE_HIER_OVERRIDES[module]
        return p if p.exists() else None

    if cluster_rtl_dir is not None:
        p = cluster_rtl_dir / f"{module}.hier"
        if p.exists():
            return p

    p = root / f"core/{module}/rtl/{module}.hier"
    return p if p.exists() else None


def get_children(module: str, model_root: str) -> List[str]:
    """Return filtered direct children of *module* (single level, on-demand).

    Backward-compatible wrapper.  Prefers the cached ``build_children_map``
    so all calls share the same parsed result.
    """
    return build_children_map(model_root).get(module, [])


# ── Full hierarchy walk ───────────────────────────────────────────────────────

def _walk(
    module: str,
    parent: Optional[str],
    cluster_rtl_dir: Optional[Path],
    root: Path,
    parent_map: Dict[str, Optional[str]],
    visited: set,
) -> None:
    """Recursive DFS walk over .hier files to populate *parent_map*."""
    if module in visited:
        return
    visited.add(module)
    parent_map[module] = parent

    # ── Locate .hier file ──────────────────────────────────────────────────────
    if module == "icore":
        hier_path: Optional[Path] = root / ICORE_HIER_REL
    elif module in MODULE_HIER_OVERRIDES:
        hier_path = root / MODULE_HIER_OVERRIDES[module]
    elif cluster_rtl_dir is not None and (cluster_rtl_dir / f"{module}.hier").exists():
        hier_path = cluster_rtl_dir / f"{module}.hier"
    else:
        candidate = root / f"core/{module}/rtl/{module}.hier"
        hier_path = candidate if candidate.exists() else None

    if not hier_path or not hier_path.exists():
        return

    children = extract_children(hier_path, module)

    # ── Determine cluster_rtl_dir for each child ───────────────────────────────
    if module == "icore":
        # icore's direct children are top-level clusters; each has its own rtl dir
        for child in children:
            if child in MODULE_HIER_OVERRIDES:
                child_dir: Optional[Path] = (root / MODULE_HIER_OVERRIDES[child]).parent
            else:
                child_dir = root / f"core/{child}/rtl"
            _walk(child, module, child_dir, root, parent_map, visited)
    else:
        # Sub-modules share the parent's cluster rtl dir (stay in same dir)
        child_dir = hier_path.parent
        for child in children:
            _walk(child, module, child_dir, root, parent_map, visited)


def build_module_parent_map(model_root: str) -> Dict[str, Optional[str]]:
    """Walk all .hier files from icore down; return ``{module: parent}``.

    Result is cached per *model_root*.

    ``icore`` maps to ``None`` (root).
    Known top-level clusters absent from icore.hier (mlc, pm) are seeded
    as conceptual children of icore with parent = ``"icore"``.
    """
    if model_root in _parent_map_cache:
        return _parent_map_cache[model_root]

    parent_map: Dict[str, Optional[str]] = {}
    visited: set = set()
    root = Path(model_root)

    _walk("icore", None, None, root, parent_map, visited)

    # Seed top-level clusters absent from icore.hier
    for cluster in _EXTRA_TOP_LEVEL_CLUSTERS:
        if cluster not in parent_map:
            child_dir = root / f"core/{cluster}/rtl"
            _walk(cluster, "icore", child_dir, root, parent_map, visited)

    _parent_map_cache[model_root] = parent_map
    return parent_map


def build_children_map(model_root: str) -> Dict[str, List[str]]:
    """Return ``{module: [direct_children]}``, built by inverting the parent map.

    Result is cached per *model_root*.
    """
    if model_root in _children_map_cache:
        return _children_map_cache[model_root]

    parent_map = build_module_parent_map(model_root)
    children_map: Dict[str, List[str]] = {}
    for module, parent in parent_map.items():
        if parent is not None:
            children_map.setdefault(parent, []).append(module)

    _children_map_cache[model_root] = children_map
    return children_map
