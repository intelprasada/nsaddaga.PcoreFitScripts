import { describe, it, expect } from "vitest";
import { shouldFireP0Burst, shouldShowReplayButton } from "../src/lib/p0Burst";

describe("p0Burst — shouldFireP0Burst", () => {
  it("fires on transition todo → done for a P0", () => {
    expect(shouldFireP0Burst("todo", "done", "P0", true)).toBe(true);
  });

  it("fires on transition in-progress → done for a P0", () => {
    expect(shouldFireP0Burst("in-progress", "done", "P0", true)).toBe(true);
  });

  it("fires on transition blocked → done for a P0", () => {
    expect(shouldFireP0Burst("blocked", "done", "P0", true)).toBe(true);
  });

  it("does NOT fire when prev is already done (re-render with no change)", () => {
    expect(shouldFireP0Burst("done", "done", "P0", true)).toBe(false);
  });

  it("does NOT fire on the FIRST mount when status was unknown", () => {
    // null = "no previous render yet" — never celebrate on initial load
    expect(shouldFireP0Burst(null, "done", "P0", true)).toBe(false);
    expect(shouldFireP0Burst(undefined, "done", "P0", true)).toBe(false);
  });

  it("does NOT fire when next status is not done (status moving sideways)", () => {
    expect(shouldFireP0Burst("todo", "in-progress", "P0", true)).toBe(false);
    expect(shouldFireP0Burst("done", "in-progress", "P0", true)).toBe(false);
  });

  it("does NOT fire for non-P0 priorities", () => {
    expect(shouldFireP0Burst("todo", "done", "P1", true)).toBe(false);
    expect(shouldFireP0Burst("todo", "done", "P2", true)).toBe(false);
    expect(shouldFireP0Burst("todo", "done", "", true)).toBe(false);
    expect(shouldFireP0Burst("todo", "done", null, true)).toBe(false);
  });

  it("is case-insensitive on priority", () => {
    expect(shouldFireP0Burst("todo", "done", "p0", true)).toBe(true);
    expect(shouldFireP0Burst("todo", "done", " P0 ", true)).toBe(true);
  });

  it("does NOT fire when gamification is opted out", () => {
    expect(shouldFireP0Burst("todo", "done", "P0", false)).toBe(false);
  });
});

describe("p0Burst — shouldShowReplayButton", () => {
  it("shows for done P0 with gamify on", () => {
    expect(shouldShowReplayButton("done", "P0", true)).toBe(true);
  });

  it("hides on non-done statuses (so reopening removes the button)", () => {
    expect(shouldShowReplayButton("todo", "P0", true)).toBe(false);
    expect(shouldShowReplayButton("in-progress", "P0", true)).toBe(false);
    expect(shouldShowReplayButton("blocked", "P0", true)).toBe(false);
  });

  it("hides for non-P0 priorities", () => {
    expect(shouldShowReplayButton("done", "P1", true)).toBe(false);
    expect(shouldShowReplayButton("done", "", true)).toBe(false);
    expect(shouldShowReplayButton("done", null, true)).toBe(false);
  });

  it("hides when gamification is off", () => {
    expect(shouldShowReplayButton("done", "P0", false)).toBe(false);
  });

  it("is case-insensitive on priority", () => {
    expect(shouldShowReplayButton("done", "p0", true)).toBe(true);
    expect(shouldShowReplayButton("done", " P0 ", true)).toBe(true);
  });
});
