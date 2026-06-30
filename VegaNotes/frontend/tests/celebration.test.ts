import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  onCelebration,
  triggerCelebration,
  _resetCelebrationListenersForTests,
} from "../src/lib/celebration";

beforeEach(() => {
  _resetCelebrationListenersForTests();
});

describe("celebration event bus", () => {
  it("delivers events to a subscribed listener", () => {
    const spy = vi.fn();
    onCelebration(spy);
    triggerCelebration({ priority: "P0", sourceId: "T-ABC" });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ priority: "P0", sourceId: "T-ABC" });
  });

  it("delivers to multiple listeners", () => {
    const a = vi.fn();
    const b = vi.fn();
    onCelebration(a);
    onCelebration(b);
    triggerCelebration({});
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("passes optional origin coordinates through unchanged", () => {
    const spy = vi.fn();
    onCelebration(spy);
    triggerCelebration({ origin: { x: 120, y: 240 } });
    expect(spy.mock.calls[0][0].origin).toEqual({ x: 120, y: 240 });
  });

  it("returns an unsubscribe function that removes the listener", () => {
    const spy = vi.fn();
    const unsub = onCelebration(spy);
    triggerCelebration({});
    expect(spy).toHaveBeenCalledTimes(1);
    unsub();
    triggerCelebration({});
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("does NOT throw when a listener throws — other listeners still fire", () => {
    const bad = vi.fn(() => { throw new Error("boom"); });
    const good = vi.fn();
    onCelebration(bad);
    onCelebration(good);
    expect(() => triggerCelebration({})).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);
  });

  it("triggerCelebration with no args fires an empty event", () => {
    const spy = vi.fn();
    onCelebration(spy);
    triggerCelebration();
    expect(spy).toHaveBeenCalledWith({});
  });
});
