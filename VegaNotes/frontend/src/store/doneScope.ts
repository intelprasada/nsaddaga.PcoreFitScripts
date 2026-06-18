/**
 * #258: per-view persistence of the "Done from active files only" toggle.
 *
 * Each view (Kanban, MyTasks, …) gets its own slot so flipping the toggle
 * in one view doesn't change the other. The persisted shape is a JSON
 * object keyed by view name; values are exactly "active" | "all".
 *
 * Storage failures (private mode, quota, etc.) are best-effort — read
 * returns the default, write is a no-op.
 */
export type DoneScope = "active" | "all";
export type DoneScopeView = "kanban" | "my-tasks";

export const DONE_SCOPE_STORAGE_KEY = "veganotes:done-scope:v1";
export const DEFAULT_DONE_SCOPE: DoneScope = "active";

type Persisted = Partial<Record<DoneScopeView, DoneScope>>;

function readAll(): Persisted {
  try {
    const raw = localStorage.getItem(DONE_SCOPE_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Persisted = {};
    for (const k of ["kanban", "my-tasks"] as const) {
      const v = (parsed as Record<string, unknown>)[k];
      if (v === "active" || v === "all") out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function loadDoneScope(view: DoneScopeView): DoneScope {
  return readAll()[view] ?? DEFAULT_DONE_SCOPE;
}

export function saveDoneScope(view: DoneScopeView, value: DoneScope): void {
  try {
    const all = readAll();
    all[view] = value;
    localStorage.setItem(DONE_SCOPE_STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* best-effort */
  }
}
