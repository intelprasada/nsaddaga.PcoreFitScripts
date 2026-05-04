// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import {
  createEditor,
  ParagraphNode,
  TextNode,
  LineBreakNode,
  $getRoot,
} from "lexical";
import {
  _editorPlainText_forTest,
  _setEditorPlainText_forTest,
  VegaTokenNode,
} from "../src/components/Editor/LexicalEditor.tsx";

// Pins the single-paragraph + LineBreakNode document model that fixes
// the doubled-newline bug (#167; regression of #165). The bug came from a
// paragraph-per-line layout: Lexical's reconciler joins non-inline
// (block-level) root children with DOUBLE_LINE_BREAK ("\n\n"), so every
// "\n" round-tripped as "\n\n" through OnChangePlugin → onChange →
// flushSave, growing the file on disk on each settle.
//
// Now: one ParagraphNode whose inline children are alternating TextNode +
// LineBreakNode, so the native `getTextContent()` returns the source
// verbatim — no JS-side rejoin, no per-keystroke walk overhead beyond
// what Lexical already does.

function mountEditor() {
  const editor = createEditor({
    namespace: "vt",
    onError: (e) => { throw e; },
    nodes: [ParagraphNode, TextNode, LineBreakNode, VegaTokenNode],
  });
  const root = document.createElement("div");
  document.body.appendChild(root);
  editor.setRootElement(root);
  return editor;
}

describe("LexicalEditor plain-text round-trip (single-paragraph model)", () => {
  const SAMPLES: string[] = [
    "single",
    "a\nb\nc",
    "first\nsecond\nthird\nfourth",
    "preserves\n\nblank\nlines",
    "trailing newline\n",
    "tab\there\nand spaces  here",
    "line with !task #id T-ABC and @alice",
    "",
  ];

  for (const src of SAMPLES) {
    it(`round-trips ${JSON.stringify(src)} losslessly`, () => {
      const editor = mountEditor();
      editor.update(() => { _setEditorPlainText_forTest(editor, src); }, { discrete: true });
      expect(_editorPlainText_forTest(editor)).toBe(src);
    });
  }

  it("does not double newlines on multi-line input (regression for #167)", () => {
    const editor = mountEditor();
    editor.update(() => { _setEditorPlainText_forTest(editor, "a\nb\nc"); }, { discrete: true });
    expect(_editorPlainText_forTest(editor)).toBe("a\nb\nc");
    expect(_editorPlainText_forTest(editor)).not.toContain("\n\n");
  });

  it("uses a single root-level ParagraphNode (not paragraph-per-line)", () => {
    const editor = mountEditor();
    editor.update(() => { _setEditorPlainText_forTest(editor, "a\nb\nc"); }, { discrete: true });
    let blocks = -1;
    editor.getEditorState().read(() => {
      blocks = $getRoot().getChildren().length;
    });
    expect(blocks).toBe(1);
  });

  it("represents line breaks as inline LineBreakNodes inside the paragraph", () => {
    const editor = mountEditor();
    editor.update(() => { _setEditorPlainText_forTest(editor, "a\nb"); }, { discrete: true });
    let inlineTypes: string[] = [];
    editor.getEditorState().read(() => {
      const para = $getRoot().getFirstChild();
      // @ts-expect-error - getChildren exists on ElementNode at runtime
      inlineTypes = (para?.getChildren?.() ?? []).map((c: { getType: () => string }) => c.getType());
    });
    // Plain text is wrapped in either TextNode (default) or VegaTokenNode
    // (subclass) depending on whether the highlighter has run; the shape
    // we care about is "text, linebreak, text".
    expect(inlineTypes.length).toBe(3);
    expect(["text", "vega-token"]).toContain(inlineTypes[0]);
    expect(inlineTypes[1]).toBe("linebreak");
    expect(["text", "vega-token"]).toContain(inlineTypes[2]);
  });
});
