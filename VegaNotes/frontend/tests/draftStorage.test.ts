import { describe, it, expect, beforeEach } from "vitest";
import {
  DRAFT_STORAGE_KEY,
  loadPersistedDrafts,
  persistDirtyDrafts,
} from "../src/store/draftStorage";

beforeEach(() => {
  try {
    localStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    /* jsdom may not expose localStorage */
  }
});

describe("draftStorage", () => {
  it("returns an empty map when storage is empty", () => {
    expect(loadPersistedDrafts()).toEqual({});
  });

  it("persists only dirty entries (body !== saved)", () => {
    persistDirtyDrafts({
      "a.md": { body: "X", saved: "Y", savedAt: 0, etag: "", proseEtag: "", tasksEtag: "" },
      "b.md": { body: "Z", saved: "Z", savedAt: 0, etag: "", proseEtag: "", tasksEtag: "" },
    });
    const raw = localStorage.getItem(DRAFT_STORAGE_KEY);
    expect(raw).toBeTruthy();
    const stored = JSON.parse(raw!);
    expect(Object.keys(stored)).toEqual(["a.md"]);
  });

  it("removes the key when no entries are dirty", () => {
    // Pre-seed something
    localStorage.setItem(DRAFT_STORAGE_KEY, "{}");
    persistDirtyDrafts({
      "a.md": { body: "Z", saved: "Z", savedAt: 0, etag: "", proseEtag: "", tasksEtag: "" },
    });
    expect(localStorage.getItem(DRAFT_STORAGE_KEY)).toBeNull();
  });

  it("rehydrates dirty entries with full shape", () => {
    persistDirtyDrafts({
      "weekly.md": {
        body: "EDITED", saved: "ORIG", savedAt: 1234,
        etag: "abc", proseEtag: "p1", tasksEtag: "t1",
      },
    });
    const out = loadPersistedDrafts();
    expect(out["weekly.md"]).toEqual({
      body: "EDITED", saved: "ORIG", savedAt: 1234,
      etag: "abc", proseEtag: "p1", tasksEtag: "t1",
    });
  });

  it("filters out clean entries on read so stale rows don't shadow fresher disk content", () => {
    // Seed an apparently-clean entry — load() should drop it.
    localStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        "clean.md": { body: "S", saved: "S", savedAt: 0, etag: "", proseEtag: "", tasksEtag: "" },
        "dirty.md": { body: "A", saved: "B", savedAt: 0, etag: "", proseEtag: "", tasksEtag: "" },
      }),
    );
    const out = loadPersistedDrafts();
    expect(Object.keys(out)).toEqual(["dirty.md"]);
  });

  it("returns {} on corrupt JSON without throwing", () => {
    localStorage.setItem(DRAFT_STORAGE_KEY, "{not json");
    expect(loadPersistedDrafts()).toEqual({});
  });

  it("ignores entries missing required string fields", () => {
    localStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        "bad1.md": { saved: "S", savedAt: 0 },          // no body
        "bad2.md": { body: 42, saved: "S" },             // body wrong type
        "ok.md":   { body: "A", saved: "B", savedAt: 0, etag: "", proseEtag: "", tasksEtag: "" },
      }),
    );
    expect(Object.keys(loadPersistedDrafts())).toEqual(["ok.md"]);
  });

  it("provides safe defaults for optional metadata fields", () => {
    localStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        "x.md": { body: "A", saved: "B" }, // savedAt/etag/proseEtag/tasksEtag missing
      }),
    );
    const e = loadPersistedDrafts()["x.md"];
    expect(e.savedAt).toBe(0);
    expect(e.etag).toBe("");
    expect(e.proseEtag).toBe("");
    expect(e.tasksEtag).toBe("");
  });
});
