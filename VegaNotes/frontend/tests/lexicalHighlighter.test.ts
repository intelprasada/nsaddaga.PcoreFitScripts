import { describe, it, expect } from "vitest";
import { _tokenizeLineForTest as lex } from "../src/components/Editor/LexicalEditor.tsx";
import { _tokenizeLineForTest as cm6 } from "../src/components/Editor/CM6Editor.tsx";

// Parity check (#165): the Lexical highlighter must emit the same set of
// token classes (order + bounds) as the CM6 highlighter. If these ever
// drift, the bake-off comparison stops being apples-to-apples.

const SAMPLES: string[] = [
  "!task ship the thing",
  "- !AR follow up with @bob",
  "trail #id T-ABC123 follow-up",
  "- #eta 2026-05-04 #priority P1 #status open",
  "ping @alice and @bob.smith for review",
  "see issue#42 maybe (no match)",
  "## My heading text",
  "(@dave) said hi",
  "#task T-XYZ12 see other",
  "#AR investigate further",
  "- #unknown thing",
  "contact me at me@example.com",
  "!task #id T-ABC do @alice with #priority P1",
  "",
  "    just indented prose with no tokens",
];

describe("Lexical ↔ CM6 highlighter parity", () => {
  for (const s of SAMPLES) {
    it(`matches CM6 for: ${JSON.stringify(s.slice(0, 60))}`, () => {
      expect(lex(s)).toEqual(cm6(s));
    });
  }
});

describe("Lexical highlighter standalone", () => {
  it("emits non-overlapping, ascending token ranges", () => {
    const ts = lex("!task #id T-ABC do @alice with #priority P1");
    for (let i = 1; i < ts.length; i++) {
      expect(ts[i].from).toBeGreaterThanOrEqual(ts[i - 1].to);
    }
    expect(ts.map((t) => t.cls)).toEqual([
      "vega-task", "vega-id", "vega-user", "vega-priority",
    ]);
  });
});
