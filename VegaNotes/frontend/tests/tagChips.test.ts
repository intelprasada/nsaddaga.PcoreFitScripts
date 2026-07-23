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
    const t = mkTask({ area: ["auth", "billing"] });
    expect(extraTagChips(t).map((c) => c.value)).toEqual(["auth", "billing"]);
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

  // #318 follow-up to #316: link tokens (url/hsd/jira/pr) have their own
  // clickable capsule via <LinkChips />; do not double-render them as
  // generic `#tag` chips.
  it("hides url/hsd/jira/pr link tokens (rendered via LinkChips instead)", () => {
    const t = mkTask({
      url:  "[Design](https://example.com/design)",
      hsd:  ["1234567", "9876543"],
      jira: "ABC-42",
      pr:   "owner/repo#7",
      area: "auth", // real extra tag — should still show
    });
    const keys = extraTagChips(t).map((c) => c.key);
    expect(keys).toEqual(["area"]);
  });

  it("hides link tokens case-insensitively", () => {
    const t = mkTask({
      URL:  "[X](https://x.example.com)",
      HSD:  "9999",
      JIRA: "ABC-1",
      PR:   "o/r#3",
    } as unknown as Record<string, string>);
    expect(extraTagChips(t)).toEqual([]);
  });
});
