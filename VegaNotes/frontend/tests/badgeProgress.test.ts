import { describe, it, expect } from "vitest";
import { parseBadgeProgress, clamp01 } from "../src/lib/badgeProgress";

describe("parseBadgeProgress", () => {
  it("returns null for null/undefined/empty", () => {
    expect(parseBadgeProgress(null)).toBeNull();
    expect(parseBadgeProgress(undefined)).toBeNull();
    expect(parseBadgeProgress("")).toBeNull();
    expect(parseBadgeProgress("   ")).toBeNull();
  });

  it("parses N/D into a clamped ratio and keeps the label (the #329 NaN bug)", () => {
    expect(parseBadgeProgress("0/1")).toEqual({ ratio: 0, label: "0/1" });
    expect(parseBadgeProgress("5/13")).toEqual({ ratio: 5 / 13, label: "5/13" });
    expect(parseBadgeProgress("1/1")).toEqual({ ratio: 1, label: "1/1" });
    // Never produce NaN — the old code did Math.min(1, "0/1") => NaN => "NaN%".
    expect(Number.isFinite(parseBadgeProgress("0/1")!.ratio)).toBe(true);
  });

  it("keeps a trailing unit label but still computes the ratio", () => {
    expect(parseBadgeProgress("3/10 day(s)")).toEqual({ ratio: 0.3, label: "3/10 day(s)" });
    expect(parseBadgeProgress("10/10 day(s)")).toEqual({ ratio: 1, label: "10/10 day(s)" });
  });

  it("clamps over-100% ratios to 1", () => {
    expect(parseBadgeProgress("15/10")!.ratio).toBe(1);
  });

  it("guards against a zero denominator", () => {
    expect(parseBadgeProgress("3/0")).toEqual({ ratio: 0, label: "3/0" });
  });

  it("treats a bare 0..1 number-string as a ratio", () => {
    expect(parseBadgeProgress("0.5")).toEqual({ ratio: 0.5, label: "0.5" });
    expect(parseBadgeProgress("1")).toEqual({ ratio: 1, label: "1" });
  });

  it("shows a 0-width bar for an unscaled bare count but keeps the label", () => {
    expect(parseBadgeProgress("7")).toEqual({ ratio: 0, label: "7" });
  });

  it("back-compat: accepts a finite number and formats a percent label", () => {
    expect(parseBadgeProgress(0.42)).toEqual({ ratio: 0.42, label: "42%" });
    expect(parseBadgeProgress(1.5)).toEqual({ ratio: 1, label: "100%" });
  });

  it("returns null for a non-finite number", () => {
    expect(parseBadgeProgress(NaN)).toBeNull();
    expect(parseBadgeProgress(Infinity)).toBeNull();
  });

  it("falls back to ratio 0 with the label for unparseable strings", () => {
    expect(parseBadgeProgress("almost there")).toEqual({ ratio: 0, label: "almost there" });
  });
});

describe("clamp01", () => {
  it("coerces non-finite to 0", () => {
    expect(clamp01(NaN)).toBe(0);
    expect(clamp01(Infinity)).toBe(0);
    expect(clamp01(-Infinity)).toBe(0);
  });
  it("clamps to [0,1]", () => {
    expect(clamp01(-1)).toBe(0);
    expect(clamp01(2)).toBe(1);
    expect(clamp01(0.3)).toBe(0.3);
  });
});
