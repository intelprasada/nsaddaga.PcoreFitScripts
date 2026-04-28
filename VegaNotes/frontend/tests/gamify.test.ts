// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest";
import { isGamifyEnabled, setGamifyEnabled, subscribeGamify } from "../src/lib/gamify";

beforeEach(() => {
  window.localStorage.clear();
});

describe("gamify opt-out", () => {
  it("defaults to enabled when nothing is stored", () => {
    expect(isGamifyEnabled()).toBe(true);
  });

  it("respects an explicit 'off'", () => {
    setGamifyEnabled(false);
    expect(window.localStorage.getItem("veganotes.gamify")).toBe("off");
    expect(isGamifyEnabled()).toBe(false);
  });

  it("toggles back on", () => {
    setGamifyEnabled(false);
    setGamifyEnabled(true);
    expect(isGamifyEnabled()).toBe(true);
    expect(window.localStorage.getItem("veganotes.gamify")).toBe("on");
  });

  it("notifies subscribers on change", () => {
    const seen: boolean[] = [];
    const unsub = subscribeGamify((v) => seen.push(v));
    setGamifyEnabled(false);
    setGamifyEnabled(true);
    unsub();
    setGamifyEnabled(false);
    expect(seen).toEqual([false, true]);
  });

  it("treats unknown raw values as enabled (lenient default)", () => {
    window.localStorage.setItem("veganotes.gamify", "garbage");
    expect(isGamifyEnabled()).toBe(true);
  });
});
