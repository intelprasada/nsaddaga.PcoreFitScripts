// Persistence layer for the editor's per-path draft buffers.
//
// Pre-existing behaviour: the App component held a `draft` map in
// `useState({})`, so every page reload, Vite HMR, or browser tab
// discard silently nuked all unsaved typing. After the user reported
// "I hit cancel on the conflict prompt and still lost my edits"
// (follow-up to design 8d), drafts are now mirrored to localStorage
// on every state change so that an SPA remount can rehydrate the
// in-flight buffers.
//
// Only *dirty* entries (`body !== saved`) are written. Clean entries
// are authoritatively re-fetched from disk on next mount, so
// persisting them would only risk shadowing fresher disk content
// (e.g. a popover write that landed while the tab was closed).

export type DraftEntryShape = {
  body: string;
  saved: string;
  savedAt: number;
  etag: string;
  proseEtag: string;
  tasksEtag: string;
};

export type DraftMapShape = Record<string, DraftEntryShape>;

// Bump the version suffix if `DraftEntryShape` changes
// incompatibly so older blobs are silently dropped on read instead of
// being mis-parsed.
export const DRAFT_STORAGE_KEY = "veganotes:drafts:v1";

/** Read and validate the persisted dirty-draft map.
 *
 *  Returns an empty map if storage is unavailable, the blob is
 *  missing/corrupt, or every persisted entry has gone clean
 *  (`body === saved`). The latter check defends against a stale
 *  localStorage row shadowing a fresher disk fetch — once an entry
 *  matches its baseline there's nothing to recover and the disk copy
 *  is the source of truth.
 */
export function loadPersistedDrafts(): DraftMapShape {
  if (typeof window === "undefined") return {};
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(DRAFT_STORAGE_KEY);
  } catch {
    return {};
  }
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== "object") return {};
  const out: DraftMapShape = {};
  for (const [path, entry] of Object.entries(parsed as Record<string, unknown>)) {
    const e = entry as Partial<DraftEntryShape> | null;
    if (!e || typeof e !== "object") continue;
    if (typeof e.body !== "string" || typeof e.saved !== "string") continue;
    if (e.body === e.saved) continue;
    out[path] = {
      body: e.body,
      saved: e.saved,
      savedAt: typeof e.savedAt === "number" ? e.savedAt : 0,
      etag: typeof e.etag === "string" ? e.etag : "",
      proseEtag: typeof e.proseEtag === "string" ? e.proseEtag : "",
      tasksEtag: typeof e.tasksEtag === "string" ? e.tasksEtag : "",
    };
  }
  return out;
}

/** Mirror the dirty subset of `draft` into localStorage.
 *
 *  Removes the storage key entirely when nothing is dirty so a clean
 *  session leaves no stale rehydration target behind. Quota errors
 *  and private-mode restrictions are swallowed — best-effort.
 */
export function persistDirtyDrafts(draft: DraftMapShape): void {
  if (typeof window === "undefined") return;
  const dirty: DraftMapShape = {};
  for (const [path, entry] of Object.entries(draft)) {
    if (entry && entry.body !== entry.saved) dirty[path] = entry;
  }
  try {
    if (Object.keys(dirty).length === 0) {
      window.localStorage.removeItem(DRAFT_STORAGE_KEY);
    } else {
      window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(dirty));
    }
  } catch {
    /* best-effort */
  }
}
