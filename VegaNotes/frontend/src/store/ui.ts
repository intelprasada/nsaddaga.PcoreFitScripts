import { create } from "zustand";
import { compileClauses } from "./dsl";

export interface FilterState {
  owner?: string;
  project?: string;
  feature?: string;
  priority?: string;
  status?: string;
  hide_done?: boolean;
  q?: string;
  /** Free-form DSL chips ("@area=fit-val", "eta>=ww18", …). */
  where?: string[];
}

interface UIState {
  filters: FilterState;
  view: "editor" | "kanban" | "agenda" | "timeline" | "calendar" | "graph" | "admin" | "my-tasks";
  set: (patch: Partial<UIState>) => void;
  patchFilters: (patch: Partial<FilterState>) => void;
  clearFilters: () => void;
  addChip: (clause: string) => void;
  removeChip: (index: number) => void;
  setChips: (chips: string[]) => void;
}

export const useUI = create<UIState>((set) => ({
  filters: { hide_done: true, where: [] },
  view: "my-tasks",
  set: (patch) => set(patch),
  patchFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
  clearFilters: () => set({ filters: { hide_done: true, where: [] } }),
  addChip: (clause) => set((s) => ({
    filters: { ...s.filters, where: [...(s.filters.where ?? []), clause] },
  })),
  removeChip: (index) => set((s) => {
    const cur = s.filters.where ?? [];
    if (index < 0 || index >= cur.length) return {};
    const next = cur.slice(0, index).concat(cur.slice(index + 1));
    return { filters: { ...s.filters, where: next } };
  }),
  setChips: (chips) => set((s) => ({ filters: { ...s.filters, where: chips } })),
}));

/**
 * Reduce the filter state into the parameter map ``api.tasks`` expects.
 * Fixed columns pass through unchanged; ``where`` chips compile via the
 * shared DSL and may produce repeated ``attr`` params (encoded as arrays
 * which the api client then unrolls into URL repeats).
 */
export function filtersToParams(
  f: FilterState,
): Record<string, string | string[] | boolean | undefined> {
  const out: Record<string, string | string[] | boolean | undefined> = {
    owner: f.owner,
    project: f.project,
    feature: f.feature,
    priority: f.priority,
    status: f.status,
    hide_done: f.hide_done,
    q: f.q,
  };
  const pairs = compileClauses(f.where ?? []);
  for (const [k, v] of pairs) {
    const cur = out[k];
    if (cur === undefined) {
      out[k] = v;
    } else if (Array.isArray(cur)) {
      cur.push(v);
    } else {
      out[k] = [String(cur), v];
    }
  }
  return out;
}
