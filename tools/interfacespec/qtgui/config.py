"""
ClusterConfig — maps cluster names to top-v, gen-dir, and fallback-gen-dir paths.

Also provides build_hierarchy_index / get_hierarchy for the xhier pipeline:
each module gets a ModuleHierInfo describing its parent, grandparent,
owning cluster, and derived pipeline paths.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .hier_utils import build_module_parent_map

# (top_v, gen_dir, fallback_gen_dir | None)
_CLUSTER_MAP: Dict[str, Tuple[str, str, Optional[str]]] = {
    "fe":   ("{root}/target/fe/gen/fe.v",    "{root}/target/fe/gen",   None),
    "msid": ("{root}/target/fe/gen/msid.v",  "{root}/target/fe/gen",   None),
    "ooo":  ("{root}/target/ooo/gen/ooo.v",  "{root}/target/ooo/gen",  "{root}/target/core/gen"),
    "exe":  ("{root}/target/exe/gen/exe.v",  "{root}/target/exe/gen",  None),
    "mlc":  ("{root}/target/mlc/gen/mlc.v",  "{root}/target/mlc/gen",  None),
    "meu":  ("{root}/target/meu/gen/meu.v",  "{root}/target/meu/gen",  None),
    "pm":   ("{root}/target/pm/gen/pm.v",    "{root}/target/pm/gen",   None),
}

CLUSTER_NAMES: List[str] = ["fe", "msid", "ooo", "exe", "mlc", "meu", "pm"]

_ICF_GLOB_MAP: Dict[str, str] = {
    "fe":   "core/fe/rtl/*.icf",
    "msid": "core/msid/rtl/*.icf",
    "ooo":  "core/ooo/rtl/*.icf",
    "exe":  "core/exe/rtl/*.icf",
    "mlc":  "core/mlc/rtl/*.icf",
    "meu":  "core/meu/rtl/*.icf",
    "pm":   "core/pm/rtl/*.icf",
}


def get_top_v(cluster: str, model_root: str) -> str:
    tpl, _, _ = _CLUSTER_MAP.get(cluster, ("{root}/target/{cluster}/gen/{cluster}.v", "", None))
    return tpl.format(root=model_root, cluster=cluster)


def get_gen_dir(cluster: str, model_root: str) -> str:
    _, tpl, _ = _CLUSTER_MAP.get(cluster, ("", "{root}/target/{cluster}/gen", None))
    return tpl.format(root=model_root, cluster=cluster)


def get_fallback_gen_dir(cluster: str, model_root: str) -> Optional[str]:
    """Return the fallback gen dir, or None if the cluster has no fallback."""
    _, _, fb = _CLUSTER_MAP.get(cluster, ("", "", None))
    if fb is None:
        return None
    return fb.format(root=model_root, cluster=cluster)


def get_icf_glob(cluster: str) -> str:
    return _ICF_GLOB_MAP.get(cluster, f"core/{cluster}/rtl/*.icf")


def get_pipeline_out_base(cluster: str, model_root: str, out_root: Optional[str] = None) -> str:
    """Base dir where pipeline run directories are created.

    The pipeline script (run_cluster_pipeline.py) creates run dirs as:
      <out_root>/<cluster>_pipeline_<ts>/        (when --out-root is supplied)
      <gen_dir>/InterfaceSpecAgent/<cluster>_pipeline_<ts>/   (default)

    So when *out_root* is provided we return it directly; otherwise we
    return the default <gen_dir>/InterfaceSpecAgent path.
    """
    if out_root:
        return out_root
    return os.path.join(get_gen_dir(cluster, model_root), "InterfaceSpecAgent")


# ── ModuleHierInfo and hierarchy index ────────────────────────────────────────

@dataclass
class ModuleHierInfo:
    """Hierarchy and pipeline metadata for a single module."""
    module:               str
    parent:               Optional[str]   # immediate .hier parent (None = root)
    grandparent:          Optional[str]   # parent's parent
    owning_cluster:       Optional[str]   # first CLUSTER_NAMES ancestor
    gen_dir:              str             # gen dir for pipeline output
    top_v:                str             # .v file for pipeline input
    fallback_gen_dir:     Optional[str]
    xhier_parent:         Optional[str]  # cluster to run *before* enrichment
    xhier_grandparent:    Optional[str]  # cluster owning the parent's gen dir


_hier_index_cache: Dict[str, Dict[str, "ModuleHierInfo"]] = {}


def _find_owning_cluster(module: str, parent_map: Dict[str, Optional[str]]) -> Optional[str]:
    """Walk up the parent chain until hitting a CLUSTER_NAMES member."""
    current: Optional[str] = module
    while current is not None:
        if current in CLUSTER_NAMES:
            return current
        current = parent_map.get(current)
    return None


def _get_gen_dir_owner(cluster: str) -> str:
    """Return the cluster that *owns* the gen_dir used by *cluster*.

    For most clusters the gen_dir is ``target/{cluster}/gen``.
    Exception: msid uses ``target/fe/gen`` → owner is ``fe``.
    """
    canonical = "{root}/target/" + cluster + "/gen"
    _, gen_tpl, _ = _CLUSTER_MAP.get(cluster, ("", canonical, None))
    if gen_tpl == canonical:
        return cluster                             # owns its own gen_dir
    # Find which cluster matches the non-canonical gen_dir template
    for c, (_, tpl, _) in _CLUSTER_MAP.items():
        if c != cluster and tpl == gen_tpl and tpl == f"{{root}}/target/{c}/gen":
            return c
    return cluster                                 # fallback: own it


def build_hierarchy_index(model_root: str) -> Dict[str, "ModuleHierInfo"]:
    """Return a dict mapping every known module to its ModuleHierInfo.

    Cached per *model_root*.
    """
    if model_root in _hier_index_cache:
        return _hier_index_cache[model_root]

    parent_map = build_module_parent_map(model_root)
    index: Dict[str, ModuleHierInfo] = {}

    for module, parent in parent_map.items():
        grandparent = parent_map.get(parent) if parent else None
        owning_cluster = _find_owning_cluster(module, parent_map)

        # Determine pipeline paths
        if owning_cluster and owning_cluster in _CLUSTER_MAP:
            gen_dir        = get_gen_dir(owning_cluster, model_root)
            fallback       = get_fallback_gen_dir(owning_cluster, model_root)
            if module != owning_cluster:
                # Sub-module: its own .v lives inside the owning cluster's gen_dir
                top_v = os.path.join(gen_dir, f"{module}.v")
            else:
                top_v = get_top_v(owning_cluster, model_root)
        else:
            # Unknown module — best-guess
            top_v      = get_top_v(module, model_root)
            gen_dir    = get_gen_dir(module, model_root)
            fallback   = get_fallback_gen_dir(module, model_root)

        # xhier_parent: the cluster whose pipeline must run before enrichment
        if owning_cluster and module != owning_cluster:
            xhier_parent: Optional[str] = owning_cluster
        else:
            xhier_parent = None

        # xhier_grandparent: the cluster that owns the parent's gen_dir
        # (needed when the parent cluster shares another cluster's gen_dir)
        if xhier_parent:
            owner = _get_gen_dir_owner(xhier_parent)
            xhier_grandparent: Optional[str] = owner if owner != xhier_parent else None
        else:
            xhier_grandparent = None

        index[module] = ModuleHierInfo(
            module=module,
            parent=parent,
            grandparent=grandparent,
            owning_cluster=owning_cluster,
            gen_dir=gen_dir,
            top_v=top_v,
            fallback_gen_dir=fallback,
            xhier_parent=xhier_parent,
            xhier_grandparent=xhier_grandparent,
        )

    _hier_index_cache[model_root] = index
    return index


def get_hierarchy(module: str, model_root: str) -> Optional["ModuleHierInfo"]:
    """Return the ModuleHierInfo for *module*, or None if not found."""
    return build_hierarchy_index(model_root).get(module)
