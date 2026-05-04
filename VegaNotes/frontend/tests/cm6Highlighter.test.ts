import { describe, it, expect } from "vitest";
import { _tokenizeLineForTest as tok } from "../src/components/Editor/CM6Editor.tsx";

// Validates that the CM6 viewport highlighter (#164) emits the same token
// classes that Classic's per-line regex highlighter does. Keeps both
// flavours visually identical without coupling the implementations.

function classes(text: string): string[] {
  return tok(text).map((t) => t.cls);
}

function span(text: string, cls: string): string | undefined {
  const t = tok(text).find((x) => x.cls === cls);
  return t ? text.slice(t.from, t.to) : undefined;
}

describe("CM6 vega highlighter", () => {
  it("highlights !task and !AR keyword tokens", () => {
    expect(span("!task do thing", "vega-task")).toBe("!task");
    expect(span("- !AR investigate", "vega-ar")).toBe("!AR");
  });

  it("highlights #id T-XXXX as a single id chip", () => {
    expect(span("trail #id T-ABC123 follow-up", "vega-id")).toBe("#id T-ABC123");
  });

  it("does not also wrap the inner #id as a generic #attr", () => {
    const cs = classes("trail #id T-ABC123 follow-up");
    expect(cs.filter((c) => c === "vega-attr")).toHaveLength(0);
  });

  it("highlights value-bearing #eta / #status / #priority including their value", () => {
    expect(span("- #eta 2026-05-04 done", "vega-eta")).toBe("#eta 2026-05-04");
    expect(span("note #status open later", "vega-status")).toBe("#status open");
    expect(span("- #priority P1 ship", "vega-priority")).toBe("#priority P1");
  });

  it("highlights @user mentions with @ included", () => {
    expect(span("ping @alice please", "vega-user")).toBe("@alice");
    expect(span("(@bob)", "vega-user")).toBe("@bob");
  });

  it("does not match @-prefix mid-word (e.g. emails)", () => {
    const cs = classes("contact me at me@example.com");
    expect(cs).not.toContain("vega-user");
  });

  it("highlights #task / #AR ref-row keywords (with optional id)", () => {
    expect(span("#task T-XYZ12 see other", "vega-task")).toBe("#task T-XYZ12");
    expect(span("#AR follow up", "vega-ar")).toBe("#AR");
  });

  it("highlights heading text after the # marker", () => {
    expect(span("## My heading", "vega-heading")).toBe("My heading");
  });

  it("treats unknown #foo as a generic vega-attr", () => {
    expect(span("- #unknown thing", "vega-attr")).toBe("#unknown");
  });

  it("does not match # mid-word", () => {
    const cs = classes("see issue#42 maybe");
    expect(cs).not.toContain("vega-attr");
  });

  it("orders tokens left-to-right and never overlaps", () => {
    const ts = tok("!task #id T-ABC do @alice with #priority P1");
    for (let i = 1; i < ts.length; i++) {
      expect(ts[i].from).toBeGreaterThanOrEqual(ts[i - 1].to);
    }
    expect(ts.map((t) => t.cls)).toEqual([
      "vega-task", "vega-id", "vega-user", "vega-priority",
    ]);
  });
});
