import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { TreeNode, TreeNote } from "../api/client";

/**
 * Stable key used to identify the "(no project)" group, since `node.project`
 * is `null` for loose root-level files.
 */
export const NO_PROJECT_KEY = "__no_project__";

export function projectKey(node: TreeNode): string {
  return node.project ?? NO_PROJECT_KEY;
}

export function projectLabel(node: TreeNode): string {
  return node.project ?? "(no project)";
}

interface SidebarPrefsState {
  /** Project keys (see `projectKey`) the user has collapsed in the sidebar. */
  collapsed: Record<string, true>;
  /** Project keys the user has hidden from the main sidebar tree. */
  hidden: Record<string, true>;

  toggleCollapse: (key: string) => void;
  collapseAll: (keys: string[]) => void;
  expandAll: () => void;
  hideProject: (key: string) => void;
  unhideProject: (key: string) => void;
  reset: () => void;
}

export const useSidebarPrefs = create<SidebarPrefsState>()(
  persist(
    (set) => ({
      collapsed: {},
      hidden: {},
      toggleCollapse: (key) =>
        set((s) => {
          const next = { ...s.collapsed };
          if (next[key]) delete next[key];
          else next[key] = true;
          return { collapsed: next };
        }),
      collapseAll: (keys) =>
        set(() => {
          const next: Record<string, true> = {};
          for (const k of keys) next[k] = true;
          return { collapsed: next };
        }),
      expandAll: () => set({ collapsed: {} }),
      hideProject: (key) =>
        set((s) => ({ hidden: { ...s.hidden, [key]: true } })),
      unhideProject: (key) =>
        set((s) => {
          const next = { ...s.hidden };
          delete next[key];
          return { hidden: next };
        }),
      reset: () => set({ collapsed: {}, hidden: {} }),
    }),
    { name: "vega:sidebar:v1" },
  ),
);

export interface SidebarPrefsSnapshot {
  collapsed: Record<string, true>;
  hidden: Record<string, true>;
}

export interface ProjectView {
  /** The original tree node. */
  node: TreeNode;
  /** Notes to render under this project (filtered by search if active). */
  notes: TreeNote[];
  /** Whether the project should render its notes (true if expanded OR search-overridden). */
  expanded: boolean;
  /** Whether this project is normally hidden (only shown here because search matched it). */
  hiddenByPref: boolean;
}

export interface AppliedSidebar {
  /** Projects to render in the main tree, in original order. */
  visibleProjects: ProjectView[];
  /** Projects hidden by user preference and NOT surfaced via search. Always returned for the popover. */
  hiddenProjects: TreeNode[];
  /** Whether a non-empty search/filter is active. */
  searchActive: boolean;
}

/**
 * Pure reducer that combines a `tree` payload with the user's persisted
 * sidebar preferences and an optional search string.  Used by `Sidebar.tsx`
 * and exercised directly by unit tests.
 *
 * Search-override rule (per issue #157): when the user types in the filter
 * box, every project (including hidden ones) that has a name OR a note
 * matching the query is added back to the visible list and rendered fully
 * expanded.  This guarantees search results are never silently dropped by a
 * collapsed/hidden state.
 */
export function applySidebarPrefs(
  tree: TreeNode[],
  prefs: SidebarPrefsSnapshot,
  search: string = "",
): AppliedSidebar {
  const q = search.trim().toLowerCase();
  const searchActive = q.length > 0;
  const matches = (text: string | null | undefined) =>
    !!text && text.toLowerCase().includes(q);

  const noteMatches = (n: TreeNote) => matches(n.title) || matches(n.path);
  const projectNameMatches = (node: TreeNode) =>
    matches(projectLabel(node));

  const visibleProjects: ProjectView[] = [];
  const hiddenProjects: TreeNode[] = [];

  for (const node of tree) {
    const key = projectKey(node);
    const isHidden = !!prefs.hidden[key];

    if (searchActive) {
      const projHit = projectNameMatches(node);
      const noteHits = node.notes.filter(noteMatches);
      const hasHit = projHit || noteHits.length > 0;
      if (hasHit) {
        visibleProjects.push({
          node,
          notes: projHit ? node.notes : noteHits,
          expanded: true,
          hiddenByPref: isHidden,
        });
      } else if (isHidden) {
        hiddenProjects.push(node);
      }
      // else: visible project with no search hits → omitted entirely
    } else {
      if (isHidden) {
        hiddenProjects.push(node);
      } else {
        visibleProjects.push({
          node,
          notes: node.notes,
          expanded: !prefs.collapsed[key],
          hiddenByPref: false,
        });
      }
    }
  }

  return { visibleProjects, hiddenProjects, searchActive };
}
