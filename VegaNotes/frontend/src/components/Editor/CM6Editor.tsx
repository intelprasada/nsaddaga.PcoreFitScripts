import { useEffect, useRef } from "react";
import { EditorState, Compartment, RangeSetBuilder } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  Decoration,
  ViewPlugin,
  drawSelection,
} from "@codemirror/view";
import type { DecorationSet, ViewUpdate } from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { vim, Vim } from "@replit/codemirror-vim";
import { useEditorPrefs } from "../../store/editorPrefs";
import type { EditorHostProps } from "./types";

// Module-level latch: only register :w → host save once, no matter how
// many CM6Editor instances mount.  Lives here (not inside the component)
// because Vim.defineEx mutates global vim state.
let vimSaveBound = false;
let pendingSaveRef: { current: (() => void) | undefined } | undefined;
function ensureVimSaveBinding(saveRef: { current: (() => void) | undefined }) {
  pendingSaveRef = saveRef;
  if (vimSaveBound) return;
  vimSaveBound = true;
  // :w / :write → host requestSave (so the existing flushSave path runs).
  // Keep the existing Mod-s keybinding too — vim users often muscle-memory
  // both.  pendingSaveRef is rebound on every mount to the latest editor
  // instance's requestSave callback.
  Vim.defineEx("write", "w", () => { pendingSaveRef?.current?.(); });
  Vim.defineEx("update", "up", () => { pendingSaveRef?.current?.(); });
}

/**
 * CodeMirror 6 prototype editor (#164 / umbrella #162).
 *
 * Why CM6:
 *   - real text rendering (not a textarea+mirror), so caret position is
 *     pixel-perfect on scroll — that's the whole motivation for the
 *     bake-off (#52, #57, #155, #166).
 *   - viewport-only DOM (handles 10k-line notes without jank).
 *   - decorations are layered on the same lines that hold the caret, so
 *     scroll never desyncs highlights from text.
 *
 * VegaNotes-specific concerns implemented here:
 *   - Per-line viewport-bounded scan with the same regex set as Classic,
 *     emitting Decoration.mark with the existing .vega-task / .vega-ar /
 *     .vega-attr / .vega-user / .vega-id / .vega-eta / .vega-status /
 *     .vega-priority / .vega-heading classes — so the prototype looks
 *     identical to Classic without duplicating styles.
 *   - Tab inserts a literal \t (matches the Classic + indent-normalisation
 *     contract used by !task / !AR sub-items, see #163).
 *   - Mod-s triggers requestSave() (no browser "Save Page" dialog).
 *   - External `value` changes (file reload from disk after NFS watcher
 *     fires) replace the doc only when they truly differ from the local
 *     buffer, so typing isn't clobbered by an in-flight reload.
 *   - `gotoLine` scrolls + caret-positions on the requested 1-based line.
 *   - `onDirtyChange(dirty)` is reported as `currentDoc !== value` after
 *     every doc transaction.
 */
export function CM6Editor({
  value,
  onChange,
  readOnly = false,
  onDirtyChange,
  requestSave,
  gotoLine,
}: EditorHostProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const requestSaveRef = useRef(requestSave);
  const onDirtyRef = useRef(onDirtyChange);
  const externalValueRef = useRef(value);
  const readOnlyCompartment = useRef(new Compartment());
  const vimCompartment = useRef(new Compartment());

  // Read vim flag from the editor-prefs store; toggling it via the
  // `Vim` chip in EditorFlavorTabs reconfigures the compartment without
  // remounting the editor (so the doc, history, and selection survive).
  const vimEnabled = useEditorPrefs((s) => s.vim);

  onChangeRef.current = onChange;
  requestSaveRef.current = requestSave;
  onDirtyRef.current = onDirtyChange;

  // Bind :w / :update once globally; rebinds the latest requestSave ref
  // on every mount so the most recently-mounted editor handles the save.
  ensureVimSaveBinding(requestSaveRef);

  useEffect(() => {
    if (!hostRef.current || viewRef.current) return;

    const updateListener = EditorView.updateListener.of((u: ViewUpdate) => {
      if (!u.docChanged) return;
      const next = u.state.doc.toString();
      onChangeRef.current(next);
      onDirtyRef.current?.(next !== externalValueRef.current);
    });

    const saveKeymap = keymap.of([
      {
        key: "Mod-s",
        preventDefault: true,
        run: () => {
          requestSaveRef.current?.();
          return true;
        },
      },
    ]);

    const state = EditorState.create({
      doc: value,
      extensions: [
        // vim() must come BEFORE other keymaps so it can intercept Esc /
        // hjkl / : etc. before defaultKeymap consumes them.  Toggled via
        // a Compartment so the user can flip it on/off without losing
        // the current document state (#168).
        vimCompartment.current.of(vimEnabled ? vim() : []),
        lineNumbers(),
        highlightActiveLineGutter(),
        // drawSelection paints CM6's own selection layer (instead of the
        // native browser one) — required for vim's visual-block (Ctrl-v)
        // rectangular selection to render across multiple lines.
        drawSelection(),
        history(),
        highlightActiveLine(),
        highlightSelectionMatches(),
        keymap.of([indentWithTab, ...defaultKeymap, ...historyKeymap, ...searchKeymap]),
        saveKeymap,
        vegaHighlighter(),
        readOnlyCompartment.current.of(EditorState.readOnly.of(readOnly)),
        EditorView.lineWrapping,
        EditorView.theme({
          "&": { height: "28rem", fontSize: "13px" },
          ".cm-content": { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' },
          ".cm-scroller": { overflow: "auto" },
        }),
        updateListener,
      ],
    });

    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // mount once; subsequent prop changes handled by dedicated effects

  // Toggle vim extension via Compartment.  No re-mount; doc + history +
  // selection all survive the toggle.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: vimCompartment.current.reconfigure(vimEnabled ? vim() : []),
    });
  }, [vimEnabled]);

  // External value change → reconcile only when it really differs from
  // what's in the editor right now (prevents NFS-watcher reload from
  // erasing in-progress typing on a re-render).
  useEffect(() => {
    externalValueRef.current = value;
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) {
      onDirtyRef.current?.(false);
      return;
    }
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
    onDirtyRef.current?.(false);
  }, [value]);

  // readOnly toggles via compartment so we don't re-mount the whole editor.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: readOnlyCompartment.current.reconfigure(
        EditorState.readOnly.of(readOnly),
      ),
    });
  }, [readOnly]);

  // Click-to-jump from sidebar / My Tasks: scroll target line to centre
  // and put the caret at its start.
  useEffect(() => {
    const view = viewRef.current;
    if (!view || !gotoLine || gotoLine < 1) return;
    const doc = view.state.doc;
    const line = Math.min(gotoLine, doc.lines);
    const info = doc.line(line);
    view.dispatch({
      selection: { anchor: info.from },
      effects: EditorView.scrollIntoView(info.from, { y: "center" }),
    });
    view.focus();
  }, [gotoLine]);

  return (
    <div
      ref={hostRef}
      data-testid="cm6-editor"
      className="vega-editor-wrap relative w-full h-[28rem] rounded border bg-white"
    />
  );
}

/* ---------- viewport highlighter ----------------------------------------- */

// Same token grammar as Classic. Order matters: more-specific value-bearing
// patterns first so the generic #attr fallback doesn't swallow #eta etc.
const ID_TOKEN_RE  = /#id\s+(T-[A-Z0-9]+)/gi;
const REF_TASK_RE  = /#task(?:\s+T-[A-Z0-9]+)?\b/gi;
const REF_AR_RE    = /#AR(?:\s+T-[A-Z0-9]+)?\b/gi;
const ETA_RE       = /#eta\s+\S+/gi;
const STATUS_RE    = /#status\s+\S+/gi;
const PRIORITY_RE  = /#priority\s+\S+/gi;
const TASK_RE      = /!task\b/g;
const AR_RE        = /!AR\b/g;
const USER_RE      = /(^|[\s([])(@[a-zA-Z][\w.-]*)/g;
const ATTR_RE      = /(^|[^A-Za-z0-9_>-])(#[a-zA-Z][\w-]*)/g;
const HEADING_RE   = /^(\s*#{1,6}\s+)(.+)$/;

interface Token { from: number; to: number; cls: string; priority: number; }

function classForLineSlice(text: string, lineStart: number, into: Token[]): void {
  // Heading (line-anchored)
  const h = HEADING_RE.exec(text);
  if (h) {
    const headFrom = lineStart + h[1].length;
    into.push({ from: headFrom, to: headFrom + h[2].length, cls: "vega-heading", priority: 1 });
  }
  pushAll(text, lineStart, ID_TOKEN_RE,  "vega-id",       9, into);
  pushAll(text, lineStart, REF_TASK_RE,  "vega-task",     8, into);
  pushAll(text, lineStart, REF_AR_RE,    "vega-ar",       8, into);
  pushAll(text, lineStart, ETA_RE,       "vega-eta",      8, into);
  pushAll(text, lineStart, STATUS_RE,    "vega-status",   8, into);
  pushAll(text, lineStart, PRIORITY_RE,  "vega-priority", 8, into);
  pushAll(text, lineStart, TASK_RE,      "vega-task",     7, into);
  pushAll(text, lineStart, AR_RE,        "vega-ar",       7, into);
  // @user uses a leading-context group; emit the @name span (group 2).
  USER_RE.lastIndex = 0;
  let mu: RegExpExecArray | null;
  while ((mu = USER_RE.exec(text))) {
    const at = lineStart + mu.index + mu[1].length;
    into.push({ from: at, to: at + mu[2].length, cls: "vega-user", priority: 6 });
  }
  // Generic #attr — must avoid mid-word matches and already-tokenised IDs.
  ATTR_RE.lastIndex = 0;
  let ma: RegExpExecArray | null;
  while ((ma = ATTR_RE.exec(text))) {
    const at = lineStart + ma.index + ma[1].length;
    into.push({ from: at, to: at + ma[2].length, cls: "vega-attr", priority: 3 });
  }
}

function pushAll(text: string, lineStart: number, re: RegExp, cls: string, priority: number, into: Token[]) {
  re.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    into.push({ from: lineStart + m.index, to: lineStart + m.index + m[0].length, cls, priority });
  }
}

/**
 * Test seam: tokenise a single line (no editor mount). Returns the
 * non-overlapping list of {from,to,cls} that the live highlighter would
 * produce for the same text, with the same priority/overlap rules.
 */
export function _tokenizeLineForTest(text: string): { from: number; to: number; cls: string }[] {
  const tokens: Token[] = [];
  classForLineSlice(text, 0, tokens);
  tokens.sort((a, b) => a.from - b.from || b.priority - a.priority);
  const out: { from: number; to: number; cls: string }[] = [];
  let lastEnd = -1;
  for (const t of tokens) {
    if (t.from < lastEnd) continue;
    out.push({ from: t.from, to: t.to, cls: t.cls });
    lastEnd = t.to;
  }
  return out;
}

function buildDecorations(view: EditorView): DecorationSet {
  const tokens: Token[] = [];
  for (const { from, to } of view.visibleRanges) {
    let pos = from;
    while (pos <= to) {
      const line = view.state.doc.lineAt(pos);
      const text = line.text;
      classForLineSlice(text, line.from, tokens);
      pos = line.to + 1;
      if (line.to >= to) break;
    }
  }
  // Sort by start, then by priority desc — RangeSetBuilder requires
  // strictly non-decreasing start positions; on ties we keep the highest
  // priority match and drop overlaps.
  tokens.sort((a, b) => a.from - b.from || b.priority - a.priority);
  const builder = new RangeSetBuilder<Decoration>();
  let lastEnd = -1;
  for (const t of tokens) {
    if (t.from < lastEnd) continue; // overlap → keep first (higher priority due to sort)
    builder.add(t.from, t.to, Decoration.mark({ class: t.cls }));
    lastEnd = t.to;
  }
  return builder.finish();
}

function vegaHighlighter() {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = buildDecorations(view);
      }
      update(u: ViewUpdate) {
        if (u.docChanged || u.viewportChanged || u.selectionSet) {
          this.decorations = buildDecorations(u.view);
        }
      }
    },
    { decorations: (v) => v.decorations },
  );
}
