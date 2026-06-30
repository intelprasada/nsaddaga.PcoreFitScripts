import { describe, it, expect, beforeEach } from "vitest";
import { shouldShowReplayButton } from "../src/lib/p0Burst";

beforeEach(() => {
  // No state to reset — pure function.
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
