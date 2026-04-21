import { create } from "zustand";

export interface FilterState {
  owner?: string;
  project?: string;
  feature?: string;
  priority?: string;
  status?: string;
  hide_done?: boolean;
  q?: string;
}

interface UIState {
  filters: FilterState;
  view: "editor" | "kanban" | "agenda" | "timeline" | "calendar" | "graph" | "admin";
  set: (patch: Partial<UIState>) => void;
  patchFilters: (patch: Partial<FilterState>) => void;
  clearFilters: () => void;
}

export const useUI = create<UIState>((set) => ({
  filters: { hide_done: true },
  view: "kanban",
  set: (patch) => set(patch),
  patchFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
  clearFilters: () => set({ filters: { hide_done: true } }),
}));
