import { describe, it, expect, beforeEach } from "vitest";
import {
  DONE_SCOPE_STORAGE_KEY,
  DEFAULT_DONE_SCOPE,
  loadDoneScope,
  saveDoneScope,
} from "../src/store/doneScope";

beforeEach(() => {
  try {
    localStorage.removeItem(DONE_SCOPE_STORAGE_KEY);
  } catch {
    /* ignore */
  }
});

describe("doneScope storage (#258)", () => {
  it("returns the default 'active' on a fresh session", () => {
    expect(loadDoneScope("kanban")).toBe(DEFAULT_DONE_SCOPE);
    expect(loadDoneScope("my-tasks")).toBe(DEFAULT_DONE_SCOPE);
    expect(DEFAULT_DONE_SCOPE).toBe("active");
  });

  it("persists per-view independently", () => {
    saveDoneScope("kanban", "all");
    expect(loadDoneScope("kanban")).toBe("all");
    expect(loadDoneScope("my-tasks")).toBe("active");

    saveDoneScope("my-tasks", "all");
    expect(loadDoneScope("kanban")).toBe("all");
    expect(loadDoneScope("my-tasks")).toBe("all");
  });

  it("round-trips both scopes", () => {
    saveDoneScope("kanban", "all");
    expect(loadDoneScope("kanban")).toBe("all");
    saveDoneScope("kanban", "active");
    expect(loadDoneScope("kanban")).toBe("active");
  });

  it("falls back to default on JSON corruption", () => {
    localStorage.setItem(DONE_SCOPE_STORAGE_KEY, "{not valid json");
    expect(loadDoneScope("kanban")).toBe(DEFAULT_DONE_SCOPE);
  });

  it("falls back to default for malformed values", () => {
    localStorage.setItem(
      DONE_SCOPE_STORAGE_KEY,
      JSON.stringify({ kanban: "bogus", "my-tasks": 42 }),
    );
    expect(loadDoneScope("kanban")).toBe(DEFAULT_DONE_SCOPE);
    expect(loadDoneScope("my-tasks")).toBe(DEFAULT_DONE_SCOPE);
  });

  it("falls back to default when payload is not an object", () => {
    localStorage.setItem(DONE_SCOPE_STORAGE_KEY, JSON.stringify("active"));
    expect(loadDoneScope("kanban")).toBe(DEFAULT_DONE_SCOPE);
    localStorage.setItem(DONE_SCOPE_STORAGE_KEY, JSON.stringify(null));
    expect(loadDoneScope("kanban")).toBe(DEFAULT_DONE_SCOPE);
    localStorage.setItem(DONE_SCOPE_STORAGE_KEY, JSON.stringify(["active"]));
    // Arrays are objects in JS — readAll iterates the known keys, both
    // missing → defaults.
    expect(loadDoneScope("kanban")).toBe(DEFAULT_DONE_SCOPE);
  });

  it("preserves the other view's value when saving one", () => {
    saveDoneScope("kanban", "all");
    saveDoneScope("my-tasks", "all");
    saveDoneScope("kanban", "active");
    expect(loadDoneScope("kanban")).toBe("active");
    expect(loadDoneScope("my-tasks")).toBe("all");
  });
});
