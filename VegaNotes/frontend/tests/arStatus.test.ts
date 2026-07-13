import { describe, it, expect } from "vitest";
import { AR_STATUS_CYCLE, AR_STATUS_STYLES, nextArStatus } from "../src/components/Tasks/arStatus";

describe("nextArStatus", () => {
  it("cycles todo → in-progress", () => {
    expect(nextArStatus("todo")).toBe("in-progress");
  });

  it("cycles in-progress → blocked", () => {
    expect(nextArStatus("in-progress")).toBe("blocked");
  });

  it("cycles blocked → done", () => {
    expect(nextArStatus("blocked")).toBe("done");
  });

  it("wraps done → todo", () => {
    expect(nextArStatus("done")).toBe("todo");
  });

  it("falls through unknown status to the first cycle entry", () => {
    // Users can put arbitrary #status values in their .md; we don't want
    // the cycle button to silently no-op on those — it should reset to a
    // known status so the user can move forward.
    expect(nextArStatus("waiting")).toBe("todo");
    expect(nextArStatus("")).toBe("todo");
  });

  it("cycles through the full loop without skipping", () => {
    const seen = new Set<string>();
    let current: string = AR_STATUS_CYCLE[0];
    for (let i = 0; i < AR_STATUS_CYCLE.length; i++) {
      seen.add(current);
      current = nextArStatus(current);
    }
    expect(seen).toEqual(new Set(AR_STATUS_CYCLE));
    // One more step wraps back to start.
    expect(current).toBe(AR_STATUS_CYCLE[0]);
  });
});

describe("AR_STATUS_STYLES", () => {
  it("covers every entry in the cycle", () => {
    for (const s of AR_STATUS_CYCLE) {
      expect(AR_STATUS_STYLES[s]).toBeTruthy();
    }
  });

  it("has a `default` fallback for unknown statuses", () => {
    expect(AR_STATUS_STYLES.default).toBeTruthy();
  });
});
