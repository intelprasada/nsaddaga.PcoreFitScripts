import { useDeferredValue, useEffect, useRef, useState } from "react";
import type { EditorHostProps } from "./types";

/**
 * Classic VegaNotes markdown editor — transparent <textarea> overlaid on a
 * syntax-highlighted <pre> mirror.
 *
 * Architecture (kept verbatim from the original NoteEditor):
 *   - keystrokes only update local state and a debounced upward push;
 *   - highlight runs through useDeferredValue so React can interrupt;
 *   - per-line LRU cache means unchanged lines never re-tokenize (#47);
 *   - the mirror is one <pre> with one highlighted string so its layout
 *     geometry matches the textarea exactly (#57);
 *   - scroll sync is rAF-throttled to avoid layout thrash on long notes (#52).
 *
 * Known caveat: textarea + mirror cannot achieve pixel-perfect caret
 * fidelity on scroll (#52, #57, #155). The CM6 (#164) and Lexical (#165)
 * tabs exist to evaluate replacements; this Classic flavor is preserved
 * unchanged so day-to-day work isn't disrupted during the bake-off.
 */
export function ClassicEditor({
  value,
  onChange,
  readOnly = false,
  onDirtyChange,
  requestSave,
}: EditorHostProps) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);

  const [local, setLocal] = useState(value);
  const lastEmitted = useRef(value);
  const flushTimer = useRef<number | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const localRef = useRef(local);
  useEffect(() => { localRef.current = local; }, [local]);

  useEffect(() => {
    if (value !== local && value !== lastEmitted.current) {
      setLocal(value);
      lastEmitted.current = value;
    }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  // Forward dirty signal to host (App-level ● indicator).
  useEffect(() => {
    onDirtyChange?.(local !== lastEmitted.current || local !== value);
  }, [local, value, onDirtyChange]);

  // Ctrl/Cmd+S inside the textarea also asks the host to save.  App.tsx
  // already binds a window-level keydown for the same combo, but we stop
  // the default browser "Save Page" prompt locally too.
  function onKeyShortcut(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      requestSave?.();
    }
  }

  const deferred = useDeferredValue(local);

  // Render the highlighted overlay. We keep the mirror as a single <pre>
  // whose innerHTML is the full highlighted string so its layout matches the
  // textarea exactly. The per-line cache (highlightLineCached) keeps the
  // tokenize cost ~constant per keystroke even for very large docs.
  useEffect(() => {
    const pre = preRef.current;
    if (!pre) return;
    const html = deferred.split("\n").map(highlightLineCached).join("\n");
    if (pre.innerHTML !== html) pre.innerHTML = html;
  }, [deferred]);

  const scheduleEmit = (next: string) => {
    if (flushTimer.current != null) window.clearTimeout(flushTimer.current);
    flushTimer.current = window.setTimeout(() => {
      flushTimer.current = null;
      lastEmitted.current = next;
      onChangeRef.current(next);
    }, 200);
  };

  const flushNow = (next: string) => {
    if (flushTimer.current != null) {
      window.clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    if (next !== lastEmitted.current) {
      lastEmitted.current = next;
      onChangeRef.current(next);
    }
  };

  useEffect(() => () => flushNow(localRef.current), []); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (next: string) => {
    setLocal(next);
    scheduleEmit(next);
  };

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const ta = e.currentTarget;
    const { selectionStart: s, selectionEnd: en, value: v } = ta;
    if (e.shiftKey) {
      const lineStart = v.lastIndexOf("\n", s - 1) + 1;
      const lineHead = v.slice(lineStart, lineStart + 4);
      const drop = lineHead.startsWith("\t")
        ? 1
        : (/^ {1,4}/.exec(lineHead)?.[0].length ?? 0);
      if (drop === 0) return;
      const next = v.slice(0, lineStart) + v.slice(lineStart + drop);
      update(next);
      requestAnimationFrame(() => ta.setSelectionRange(s - drop, en - drop));
    } else {
      const next = v.slice(0, s) + "\t" + v.slice(en);
      update(next);
      requestAnimationFrame(() => ta.setSelectionRange(s + 1, s + 1));
    }
  }

  // rAF-throttled scroll sync — coalesces bursts into one DOM write per frame.
  const scrollScheduled = useRef(false);
  function onScroll(e: React.UIEvent<HTMLTextAreaElement>) {
    if (!preRef.current) return;
    const ta = e.currentTarget;
    if (scrollScheduled.current) return;
    scrollScheduled.current = true;
    requestAnimationFrame(() => {
      scrollScheduled.current = false;
      const pre = preRef.current;
      if (!pre) return;
      pre.scrollTop = ta.scrollTop;
      pre.scrollLeft = ta.scrollLeft;
    });
  }

  return (
    <div className={`vega-editor-wrap relative w-full h-[28rem] rounded border bg-white ${readOnly ? "opacity-80 cursor-not-allowed" : ""}`}>
      <pre
        ref={preRef}
        aria-hidden="true"
        className="vega-editor-pre absolute inset-0 m-0 p-3 pointer-events-none"
      />
      <textarea
        ref={taRef}
        value={local}
        onChange={(e) => { if (!readOnly) update(e.target.value); }}
        onKeyDown={(e) => {
          if (readOnly) return;
          onKeyShortcut(e);
          onKeyDown(e);
        }}
        onScroll={onScroll}
        onBlur={(e) => { if (!readOnly) flushNow(e.currentTarget.value); }}
        readOnly={readOnly}
        spellCheck={false}
        wrap="off"
        className={`vega-editor-ta absolute inset-0 w-full h-full m-0 p-3 bg-transparent text-transparent caret-slate-900 resize-none outline-none overflow-auto ${readOnly ? "cursor-not-allowed select-text" : ""}`}
      />
    </div>
  );
}

/* ---------- highlighter (per-line, cached) -------------------------------- */

const HEADING_LINE_RE = /^(\s*)(#{1,6})(\s+)(.*)$/;
const TASK_RE      = /!task\b/g;
const AR_RE        = /!AR\b/g;
// Task ID token: #id T-XXXXXXXX  (must run before ATTR_RE and REF_* so it isn't split)
const ID_TOKEN_RE  = /#id\s+(T-[A-Z0-9]+)/gi;
// Ref-row keywords: #task/#AR followed by optional T-XXXXXX id.
// Run before ATTR_RE so they aren't re-wrapped as generic vega-attr.
const REF_TASK_RE  = /#task(?:\s+T-[A-Z0-9]+)?\b/gi;
const REF_AR_RE    = /#AR(?:\s+T-[A-Z0-9]+)?\b/gi;
// Value-bearing attrs: keyword + next non-space token.
// Must run before ATTR_RE so the value word isn't left as plain text.
const ETA_RE       = /#eta\s+(\S+)/gi;
const STATUS_RE    = /#status\s+(\S+)/gi;
const PRIORITY_RE  = /#priority\s+(\S+)/gi;
const USER_RE      = /(^|[\s([])@([a-zA-Z][\w.-]*)/g;
const ATTR_RE      = /#([a-zA-Z][\w-]*)/g;

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}

function highlightLine(line: string): string {
  let s = escapeHtml(line);
  const m = HEADING_LINE_RE.exec(s);
  if (m) {
    s = `${m[1]}${m[2]}${m[3]}<span class="vega-heading">${m[4]}</span>`;
  }
  s = s.replace(TASK_RE, '<span class="vega-task">!task</span>');
  s = s.replace(AR_RE, '<span class="vega-ar">!AR</span>');
  // #id T-XXXXXX: highlight entire token as a monospace chip.
  // Must run before REF_* and ATTR_RE so '#id' is not re-wrapped.
  s = s.replace(ID_TOKEN_RE, '<span class="vega-id">#id $1</span>');
  // Ref-row keywords: #task [T-XXXX] → emerald, #AR [T-XXXX] → amber.
  // Run before ATTR_RE so the already-wrapped span text isn't re-matched.
  s = s.replace(REF_TASK_RE, (m2) => `<span class="vega-task">${m2}</span>`);
  s = s.replace(REF_AR_RE,   (m2) => `<span class="vega-ar">${m2}</span>`);
  // Value-bearing attrs: show keyword + value as a single styled token.
  s = s.replace(ETA_RE,      (_m, v) => `<span class="vega-eta">#eta ${v}</span>`);
  s = s.replace(STATUS_RE,   (_m, v) => `<span class="vega-status">#status ${v}</span>`);
  s = s.replace(PRIORITY_RE, (_m, v) => `<span class="vega-priority">#priority ${v}</span>`);
  s = s.replace(USER_RE, (_m, lead, name) =>
    `${lead}<span class="vega-user">@${name}</span>`);
  s = s.replace(ATTR_RE, (m2, name, off, full) => {
    const prev = full[off - 1];
    // Skip if preceded by an alphanumeric (mid-word) or by '>' (already inside a span).
    if (prev && /[A-Za-z0-9_>-]/.test(prev)) return m2;
    return `<span class="vega-attr">#${name}</span>`;
  });
  return s;
}

// Bounded LRU cache. Most lines repeat unchanged across keystrokes, so the
// cache lifts ~99% of the work in steady-state typing on large notes.
const LINE_CACHE_MAX = 8192;
const lineCache = new Map<string, string>();

function highlightLineCached(line: string): string {
  const hit = lineCache.get(line);
  if (hit !== undefined) {
    // LRU touch: re-insert to move to most-recent end.
    lineCache.delete(line);
    lineCache.set(line, hit);
    return hit;
  }
  const out = highlightLine(line);
  lineCache.set(line, out);
  if (lineCache.size > LINE_CACHE_MAX) {
    // Evict oldest (Map iterates in insertion order).
    const firstKey = lineCache.keys().next().value as string | undefined;
    if (firstKey !== undefined) lineCache.delete(firstKey);
  }
  return out;
}

// Exported for tests / external benchmarks. Renders the full document once,
// not used in the hot render path (which patches per-line via the cache).
export function highlight(src: string): string {
  return src.split("\n").map(highlightLineCached).join("\n");
}
