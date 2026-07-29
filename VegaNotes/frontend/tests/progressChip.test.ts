/**
 * #320: unit tests for the recurring #progress chip helpers.
 */
import { describe, it, expect } from "vitest";
import {
  parseProgressValue,
  progressColor,
  PROGRESS_COLOR_CLASS,
  formatProgressChip,
  formatProgressPercent,
  sparklinePoints,
  trendBetween,
} from "../src/lib/progressChip";

describe("parseProgressValue", () => {
  it("parses a bare counter", () => {
    expect(parseProgressValue("42")).toEqual({
      numerator: 42, denominator: null, label: null, percent: null,
    });
  });

  it("parses a ratio and computes percent", () => {
    expect(parseProgressValue("30/54")).toEqual({
      numerator: 30, denominator: 54, label: null, percent: 56,
    });
  });

  it("parses a ratio with a trailing label word", () => {
    expect(parseProgressValue("30/54 fixed")).toEqual({
      numerator: 30, denominator: 54, label: "fixed", percent: 56,
    });
  });

  it("tolerates surrounding whitespace", () => {
    expect(parseProgressValue("  12/35 fixed  ")).toEqual({
      numerator: 12, denominator: 35, label: "fixed", percent: 34,
    });
  });

  it("returns null for garbage", () => {
    for (const bad of ["", "abc", "12/xx", "12/35/7", "two words here", "1/0"]) {
      expect(parseProgressValue(bad)).toBeNull();
    }
  });

  it("rejects denominator == 0", () => {
    expect(parseProgressValue("5/0")).toBeNull();
  });

  it("caps percent above 100 for over-achieved metrics", () => {
    const p = parseProgressValue("120/100")!;
    expect(p.percent).toBe(120);
  });
});

describe("progressColor", () => {
  it("returns blue for counter-only", () => {
    expect(progressColor(parseProgressValue("42")!)).toBe("blue");
  });
  it("returns red below 25%", () => {
    expect(progressColor(parseProgressValue("5/100")!)).toBe("red");
  });
  it("returns amber in 25-74%", () => {
    expect(progressColor(parseProgressValue("25/100")!)).toBe("amber");
    expect(progressColor(parseProgressValue("74/100")!)).toBe("amber");
  });
  it("returns green in 75-99%", () => {
    expect(progressColor(parseProgressValue("75/100")!)).toBe("green");
    expect(progressColor(parseProgressValue("99/100")!)).toBe("green");
  });
  it("returns gold at 100% and above", () => {
    expect(progressColor(parseProgressValue("100/100")!)).toBe("gold");
    expect(progressColor(parseProgressValue("110/100")!)).toBe("gold");
  });
});

describe("PROGRESS_COLOR_CLASS", () => {
  it("has a class for every color band", () => {
    for (const c of ["red", "amber", "green", "gold", "blue"] as const) {
      expect(PROGRESS_COLOR_CLASS[c]).toMatch(/bg-/);
      expect(PROGRESS_COLOR_CLASS[c]).toMatch(/text-/);
    }
  });
});

describe("formatProgressChip / formatProgressPercent", () => {
  it("renders `N/D` for a ratio", () => {
    expect(formatProgressChip(parseProgressValue("30/54")!)).toBe("30/54");
    expect(formatProgressPercent(parseProgressValue("30/54")!)).toBe("56%");
  });
  it("renders bare `N` for a counter (no percent)", () => {
    expect(formatProgressChip(parseProgressValue("42")!)).toBe("42");
    expect(formatProgressPercent(parseProgressValue("42")!)).toBeNull();
  });
});

describe("sparklinePoints", () => {
  it("returns a flat dash for a single point", () => {
    expect(sparklinePoints([50], 32, 10)).toBe("0,5 32,5");
  });
  it("returns empty for empty input", () => {
    expect(sparklinePoints([], 32, 10)).toBe("");
  });
  it("plots monotonically increasing series in expected shape", () => {
    const pts = sparklinePoints([0, 50, 100], 32, 10).split(" ");
    expect(pts).toHaveLength(3);
    // First y should be at the baseline (height); last at the ceiling (0).
    const [firstX, firstY] = pts[0].split(",").map(Number);
    const [lastX, lastY] = pts[2].split(",").map(Number);
    expect(firstX).toBe(0);
    expect(lastX).toBe(32);
    expect(firstY).toBeGreaterThan(lastY);
  });
  it("uses ceiling of max(100, ...values) so over-100 spikes still fit", () => {
    const pts = sparklinePoints([100, 200], 32, 10).split(" ");
    const [, y1] = pts[0].split(",").map(Number);
    const [, y2] = pts[1].split(",").map(Number);
    // The 200 point sits at the top; the 100 point sits at the midpoint.
    expect(y2).toBeCloseTo(0, 1);
    expect(y1).toBeCloseTo(5, 1);
  });
});

describe("trendBetween", () => {
  const p = (v: string) => parseProgressValue(v)!;
  it("returns flat when no predecessor", () => {
    expect(trendBetween(null, p("12/35"))).toBe("flat");
  });
  it("returns up when percent grew", () => {
    expect(trendBetween(p("12/35"), p("24/35"))).toBe("up");
  });
  it("returns down when percent shrank", () => {
    expect(trendBetween(p("30/54"), p("12/54"))).toBe("down");
  });
  it("returns flat when percent held steady", () => {
    expect(trendBetween(p("12/35"), p("24/70"))).toBe("flat");
  });
  it("compares counters when denominators are absent", () => {
    expect(trendBetween(p("10"), p("20"))).toBe("up");
    expect(trendBetween(p("20"), p("10"))).toBe("down");
  });
});
