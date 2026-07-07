import { describe, it, expect } from "vitest";
import { extraTagChips } from "../src/lib/tagChips";
import type { Task } from "../src/api/client";

function mkTask(attrs: Record<string, string | string[]>): Task {
  return {
    id: 1,
    kind: "task",
    slug: "test",
    title: "test",
    status: "todo",
    eta: null,
    task_uuid: null,
    owners: [],
    projects: [],
    features: [],
    attrs,
    line: 1,
    note_id: 1,
    children: [],
  } as unknown as Task;
}

describe("extraTagChips (issue #275)", () => {
  it("returns nothing when attrs are all reserved", () => {
    const t = mkTask({
      priority: "P0",
      eta: "2026-04-24",
      owner: ["alice"],
      project: ["gfc"],
      status: "todo",
      note: ["did stuff"],
    });
    expect(extraTagChips(t)).toEqual([]);
  });

  it("emits bare presence chips for empty-value attrs", () => {
    const t = mkTask({ gfc: "", urgent: "" });
    expect(extraTagChips(t)).toEqual([
      { key: "gfc", value: "", reactKey: "gfc::0::" },
      { key: "urgent", value: "", reactKey: "urgent::0::" },
    ]);
  });

  it("emits value chips for non-empty attrs", () => {
    const t = mkTask({ risk: "high" });
    expect(extraTagChips(t)).toEqual([
      { key: "risk", value: "high", reactKey: "risk::0::high" },
    ]);
  });

  it("expands multi-value arrays to one chip each", () => {
    const t = mkTask({ jira: ["ABC-1", "ABC-2"] });
    expect(extraTagChips(t).map((c) => c.value)).toEqual(["ABC-1", "ABC-2"]);
  });

  it("mixes empty and non-empty in an array", () => {
    const t = mkTask({ gfc: ["", "b0"] });
    const chips = extraTagChips(t);
    expect(chips).toHaveLength(2);
    expect(chips[0]).toEqual({ key: "gfc", value: "", reactKey: "gfc::0::" });
    expect(chips[1]).toEqual({ key: "gfc", value: "b0", reactKey: "gfc::1::b0" });
  });

  it("skips reserved keys but keeps unknown ones alongside", () => {
    const t = mkTask({
      priority: "P0",
      owner: ["alice"],
      gfc: "",
      urgent: "",
      area: "auth",
    });
    const keys = extraTagChips(t).map((c) => c.key).sort();
    expect(keys).toEqual(["area", "gfc", "urgent"]);
  });

  it("is case-insensitive for reserved-key filtering", () => {
    const t = mkTask({ Priority: "P0", GFC: "" } as unknown as Record<string, string>);
    // Priority (uppercase) should still be hidden; GFC still shown.
    const keys = extraTagChips(t).map((c) => c.key);
    expect(keys).toEqual(["GFC"]);
  });

  it("handles missing attrs gracefully", () => {
    const t = mkTask({} as Record<string, string>);
    expect(extraTagChips(t)).toEqual([]);
  });
});
