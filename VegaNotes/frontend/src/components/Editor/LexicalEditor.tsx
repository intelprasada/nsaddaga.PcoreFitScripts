import { useEffect, useRef } from "react";
import {
  $getRoot,
  $createParagraphNode,
  $createTextNode,
  $createLineBreakNode,
  $isLineBreakNode,
  TextNode,
  COMMAND_PRIORITY_HIGH,
  KEY_DOWN_COMMAND,
  type LexicalEditor as LexicalEditorType,
  type SerializedTextNode,
  type EditorConfig,
  type Spread,
} from "lexical";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { PlainTextPlugin } from "@lexical/react/LexicalPlainTextPlugin";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import type { EditorHostProps } from "./types";

/**
 * Lexical prototype editor (#165 / umbrella #162).
 *
 * Document model: a SINGLE root-level ParagraphNode whose inline children
 * are alternating `TextNode`s and `LineBreakNode`s — one TextNode per
 * source line, separated by inline LineBreakNodes.
 *
 * Why this shape (vs. the obvious "one ParagraphNode per line"):
 *   * Lexical's reconciler joins **non-inline (block-level) children** of
 *     the root with `DOUBLE_LINE_BREAK` ("\n\n").  A paragraph-per-line
 *     layout therefore made `$getRoot().getTextContent()` return doubled
 *     newlines, which fed back through `OnChangePlugin → onChange →
 *     flushSave` and grew the file on disk on every settle (#167).
 *   * Inline siblings inside a single paragraph are concatenated WITHOUT
 *     `DOUBLE_LINE_BREAK`; `LineBreakNode.getTextContent()` returns "\n"
 *     natively (see node_modules/lexical/Lexical.dev.mjs:4639).  So the
 *     single-paragraph + LineBreakNode model lets the native fast-path
 *     `getTextContent()` return the correct round-trip text with zero
 *     post-processing on the JS side.
 *   * `@lexical/plain-text` already routes Enter through
 *     `INSERT_LINE_BREAK_COMMAND` rather than splitting the paragraph,
 *     so user typing produces exactly this shape natively — only the
 *     initial mount/value-sync code has to match it.
 *
 * Other concerns:
 *   * Mod-s is intercepted via `KEY_DOWN_COMMAND` at HIGH priority so
 *     the browser's "Save Page" prompt never fires.
 *   * External value changes (NFS reload from disk) replace the editor
 *     state only when the on-screen text actually differs, so typing
 *     isn't clobbered by an in-flight reload.
 *   * A NodeTransform on TextNode scans every text fragment for the
 *     same VegaNotes tokens as Classic + CM6 and replaces matched
 *     substrings with `VegaTokenNode` (a TextNode subclass whose DOM
 *     wraps in <span class="vega-…">) — visuals come straight from
 *     globals.css with no duplication.  Because each line lives in its
 *     own TextNode separated by LineBreakNodes, line-anchored regexes
 *     (^...$) work without surprises.
 *
 * Caveats vs. CM6:
 *   * No native viewport virtualisation — large notes (10k+ lines) will
 *     paint slower than CM6.
 *   * No line-numbers gutter / search panel / vim keymap out of the box.
 */
export function LexicalEditor(props: EditorHostProps) {
  const initialConfig = {
    namespace: "veganotes-lexical",
    onError: (e: Error) => { throw e; },
    nodes: [VegaTokenNode],
    editable: !props.readOnly,
    editorState: null, // populated by the value-sync plugin on mount
  };

  return (
    <div
      data-testid="lexical-editor"
      className="vega-editor-wrap relative w-full h-[28rem] rounded border bg-white overflow-auto"
    >
      <LexicalComposer initialConfig={initialConfig}>
        <PlainTextPlugin
          contentEditable={
            <ContentEditable
              className="vega-lexical-content outline-none p-3 h-full font-mono text-[13px] whitespace-pre-wrap"
              ariaLabel="Note editor"
            />
          }
          placeholder={null}
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <ValueSyncPlugin
          value={props.value}
          onChange={props.onChange}
          onDirtyChange={props.onDirtyChange}
        />
        <SaveShortcutPlugin requestSave={props.requestSave} />
        <ReadOnlySyncPlugin readOnly={!!props.readOnly} />
        <HighlighterPlugin />
        <GotoLinePlugin gotoLine={props.gotoLine} />
        <OnChangeReporter onChange={props.onChange} />
      </LexicalComposer>
    </div>
  );
}

/* ---------- token node ---------------------------------------------------- */

type SerializedVegaTokenNode = Spread<{ cls: string }, SerializedTextNode>;

export class VegaTokenNode extends TextNode {
  __cls: string;

  static getType(): string { return "vega-token"; }
  static clone(node: VegaTokenNode): VegaTokenNode {
    return new VegaTokenNode(node.__text, node.__cls, node.__key);
  }

  constructor(text: string, cls: string, key?: string) {
    super(text, key);
    this.__cls = cls;
  }

  createDOM(config: EditorConfig): HTMLElement {
    const dom = super.createDOM(config);
    dom.classList.add(this.__cls);
    return dom;
  }
  updateDOM(prev: VegaTokenNode, dom: HTMLElement, config: EditorConfig): boolean {
    // `super.updateDOM` is typed as accepting `this`; in practice TextNode's
    // implementation only reads text/format from `prev`, so the cast is safe.
    const updated = super.updateDOM(prev as unknown as this, dom, config);
    if (prev.__cls !== this.__cls) {
      dom.classList.remove(prev.__cls);
      dom.classList.add(this.__cls);
    }
    return updated;
  }
  static importJSON(json: SerializedVegaTokenNode): VegaTokenNode {
    const node = new VegaTokenNode(json.text, json.cls);
    node.setFormat(json.format);
    node.setDetail(json.detail);
    node.setMode(json.mode);
    node.setStyle(json.style);
    return node;
  }
  exportJSON(): SerializedVegaTokenNode {
    return { ...super.exportJSON(), type: "vega-token", cls: this.__cls, version: 1 };
  }
}

/* ---------- highlighter --------------------------------------------------- */

interface ScanHit { start: number; end: number; cls: string; priority: number; }

const HEADING_RE = /^(\s*#{1,6}\s+)(.+)$/;
const ID_TOKEN_RE  = /#id\s+T-[A-Z0-9]+/gi;
const REF_TASK_RE  = /#task(?:\s+T-[A-Z0-9]+)?\b/gi;
const REF_AR_RE    = /#AR(?:\s+T-[A-Z0-9]+)?\b/gi;
const ETA_RE       = /#eta\s+\S+/gi;
const STATUS_RE    = /#status\s+\S+/gi;
const PRIORITY_RE  = /#priority\s+\S+/gi;
const TASK_RE      = /!task\b/g;
const AR_RE        = /!AR\b/g;
const USER_RE      = /(^|[\s([])(@[a-zA-Z][\w.-]*)/g;
const ATTR_RE      = /(^|[^A-Za-z0-9_>-])(#[a-zA-Z][\w-]*)/g;

function scanTokens(text: string): ScanHit[] {
  const hits: ScanHit[] = [];
  const h = HEADING_RE.exec(text);
  if (h) hits.push({ start: h[1].length, end: h[1].length + h[2].length, cls: "vega-heading", priority: 1 });
  push(hits, text, ID_TOKEN_RE,  "vega-id",       9);
  push(hits, text, REF_TASK_RE,  "vega-task",     8);
  push(hits, text, REF_AR_RE,    "vega-ar",       8);
  push(hits, text, ETA_RE,       "vega-eta",      8);
  push(hits, text, STATUS_RE,    "vega-status",   8);
  push(hits, text, PRIORITY_RE,  "vega-priority", 8);
  push(hits, text, TASK_RE,      "vega-task",     7);
  push(hits, text, AR_RE,        "vega-ar",       7);
  USER_RE.lastIndex = 0;
  let mu: RegExpExecArray | null;
  while ((mu = USER_RE.exec(text))) {
    const at = mu.index + mu[1].length;
    hits.push({ start: at, end: at + mu[2].length, cls: "vega-user", priority: 6 });
  }
  ATTR_RE.lastIndex = 0;
  let ma: RegExpExecArray | null;
  while ((ma = ATTR_RE.exec(text))) {
    const at = ma.index + ma[1].length;
    hits.push({ start: at, end: at + ma[2].length, cls: "vega-attr", priority: 3 });
  }
  hits.sort((a, b) => a.start - b.start || b.priority - a.priority);
  const out: ScanHit[] = [];
  let lastEnd = -1;
  for (const t of hits) {
    if (t.start < lastEnd) continue;
    out.push(t);
    lastEnd = t.end;
  }
  return out;
}

function push(into: ScanHit[], text: string, re: RegExp, cls: string, priority: number) {
  re.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    into.push({ start: m.index, end: m.index + m[0].length, cls, priority });
  }
}

/** Test seam: same shape as CM6's _tokenizeLineForTest for parity tests. */
export function _tokenizeLineForTest(text: string): { from: number; to: number; cls: string }[] {
  return scanTokens(text).map((t) => ({ from: t.start, to: t.end, cls: t.cls }));
}

function HighlighterPlugin() {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerNodeTransform(TextNode, (node) => {
      // Skip our own subclass — registerNodeTransform fires on exact type
      // matches but defensive guard in case registration semantics shift.
      if (node instanceof VegaTokenNode) return;
      const text = node.getTextContent();
      const hits = scanTokens(text);
      if (hits.length === 0) return;
      // Split current TextNode into [pre, token, post]+ replacements.
      // Lexical's TextNode.splitText(...offsets) returns the resulting
      // array; we then walk and replace each matched fragment with a
      // VegaTokenNode while leaving plain fragments untouched.
      const offsets: number[] = [];
      for (const t of hits) {
        if (t.start > 0) offsets.push(t.start);
        offsets.push(t.end);
      }
      // De-duplicate + clamp into doc bounds; splitText requires strict
      // ascending positions in (0, length).
      const clean = Array.from(new Set(offsets.filter((o) => o > 0 && o < text.length))).sort((a, b) => a - b);
      const parts = clean.length ? node.splitText(...clean) : [node];
      // After split, positions in `parts` line up with [0, off1, off2, ...].
      let cursor = 0;
      for (const part of parts) {
        const len = part.getTextContent().length;
        const partStart = cursor;
        const partEnd = cursor + len;
        const matched = hits.find((t) => t.start === partStart && t.end === partEnd);
        if (matched && !(part instanceof VegaTokenNode)) {
          const replacement = new VegaTokenNode(part.getTextContent(), matched.cls);
          part.replace(replacement);
        }
        cursor = partEnd;
      }
    });
  }, [editor]);
  return null;
}

/* ---------- value <-> doc sync ------------------------------------------- */

function ValueSyncPlugin({
  value,
  onDirtyChange,
}: {
  value: string;
  onChange: (v: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [editor] = useLexicalComposerContext();
  const lastSetRef = useRef<string | null>(null);

  useEffect(() => {
    const current = editorPlainText(editor);
    if (current === value) {
      lastSetRef.current = value;
      onDirtyChange?.(false);
      return;
    }
    setEditorPlainText(editor, value);
    lastSetRef.current = value;
    onDirtyChange?.(false);
  }, [editor, value, onDirtyChange]);

  return null;
}

function OnChangeReporter({ onChange }: { onChange: (v: string) => void }) {
  const [editor] = useLexicalComposerContext();
  return (
    <OnChangePlugin
      onChange={() => {
        // Read outside of the editor.update() callback; getTextContent is
        // cheap and using a microtask defer-friendly read avoids fighting
        // the active update cycle.
        const text = editorPlainText(editor);
        onChange(text);
      }}
    />
  );
}

function ReadOnlySyncPlugin({ readOnly }: { readOnly: boolean }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => { editor.setEditable(!readOnly); }, [editor, readOnly]);
  return null;
}

function SaveShortcutPlugin({ requestSave }: { requestSave?: () => void }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    return editor.registerCommand(
      KEY_DOWN_COMMAND,
      (e: KeyboardEvent) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          requestSave?.();
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor, requestSave]);
  return null;
}

function GotoLinePlugin({ gotoLine }: { gotoLine?: number }) {
  const [editor] = useLexicalComposerContext();
  useEffect(() => {
    if (!gotoLine || gotoLine < 1) return;
    editor.update(() => {
      const root = $getRoot();
      const para = root.getFirstChild();
      if (!para) return;
      // Single-paragraph layout: walk inline children counting LineBreakNodes
      // to locate the start of the requested 1-based source line. Selection
      // placement / caret is best-effort; the visible scroll is what matters
      // for the click-to-jump UX.
      const want = gotoLine - 1;
      let lineIdx = 0;
      let target: ReturnType<typeof root.getFirstChild> = para;
      // @ts-expect-error - getChildren exists on ElementNode at runtime
      const inline = (para.getChildren?.() ?? []) as Array<ReturnType<typeof root.getFirstChild>>;
      for (const child of inline) {
        if (lineIdx === want) { target = child; break; }
        if (child && $isLineBreakNode(child)) lineIdx++;
      }
      if (!target) target = para;
      const dom = editor.getElementByKey(target.getKey());
      if (dom && "scrollIntoView" in dom) {
        (dom as HTMLElement).scrollIntoView({ block: "center" });
      }
    });
  }, [editor, gotoLine]);
  return null;
}

/* ---------- helpers ------------------------------------------------------- */

/* ---------- helpers (test-exported) -------------------------------------- */

export function _editorPlainText_forTest(editor: LexicalEditorType): string {
  return editorPlainText(editor);
}
export function _setEditorPlainText_forTest(editor: LexicalEditorType, value: string): void {
  setEditorPlainText(editor, value);
}

function editorPlainText(editor: LexicalEditorType): string {
  let text = "";
  editor.getEditorState().read(() => {
    // Single-paragraph layout: native `getTextContent()` is the fast path.
    // The root's only block child is one ParagraphNode whose inline children
    // (TextNodes + LineBreakNodes) are concatenated WITHOUT
    // `DOUBLE_LINE_BREAK`, so this returns the source markdown verbatim.
    text = $getRoot().getTextContent();
  });
  return text;
}

function setEditorPlainText(editor: LexicalEditorType, value: string): void {
  editor.update(() => {
    const root = $getRoot();
    root.clear();
    const para = $createParagraphNode();
    const lines = value.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].length > 0) para.append($createTextNode(lines[i]));
      if (i < lines.length - 1) para.append($createLineBreakNode());
    }
    root.append(para);
  });
}
