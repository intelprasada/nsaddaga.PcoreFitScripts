import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
}

/**
 * VegaNotes markdown editor.
 *
 * Implementation: a transparent <textarea> stacked on top of a syntax-
 * highlighted <pre> mirror. To keep typing smooth on large notes, the
 * editor manages its own local string state and only pushes upward on a
 * short debounce (or on blur / unmount). The highlight pass is fed via
 * React's `useDeferredValue` so it can be skipped when input is bursty.
 */
export function NoteEditor({ value, onChange }: Props) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);

  // Local string keeps keystrokes off the App-level state path.
  const [local, setLocal] = useState(value);
  // Track the last value we pushed up so an incoming prop change that just
  // mirrors our own emission doesn't clobber the cursor.
  const lastEmitted = useRef(value);
  const flushTimer = useRef<number | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // Mirror `local` into a ref so the unmount cleanup (which has a stale
  // closure on `local` and runs AFTER React has nulled DOM refs) can still
  // read the most recent typed text. Without this, switching tabs while a
  // sub-200ms edit was pending lost the in-flight characters.
  const localRef = useRef(local);
  useEffect(() => { localRef.current = local; }, [local]);

  // Sync local <- prop only on a real external swap (path switch, server
  // reload, etc.), never on echoes of our own debounced upward push.
  useEffect(() => {
    if (value !== local && value !== lastEmitted.current) {
      setLocal(value);
      lastEmitted.current = value;
    }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  // Render the highlighted overlay from a deferred copy so React can
  // interrupt and prioritize keystrokes.
  const deferred = useDeferredValue(local);
  const html = useMemo(() => highlight(deferred), [deferred]);
  useEffect(() => { if (preRef.current) preRef.current.innerHTML = html; }, [html]);

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

  // Make sure unmount (tab switch / new note) doesn't drop the in-flight edit.
  // Read from `localRef` (always current) — the DOM textarea ref has already
  // been detached by React at this point, and a closure on `local` would be
  // stuck at the initial-mount value.
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

  function onScroll(e: React.UIEvent<HTMLTextAreaElement>) {
    if (!preRef.current) return;
    preRef.current.scrollTop = e.currentTarget.scrollTop;
    preRef.current.scrollLeft = e.currentTarget.scrollLeft;
  }

  return (
    <div className="vega-editor-wrap relative w-full h-[28rem] rounded border bg-white">
      <pre
        ref={preRef}
        aria-hidden="true"
        className="vega-editor-pre absolute inset-0 m-0 p-3 overflow-auto whitespace-pre-wrap break-words pointer-events-none"
      />
      <textarea
        ref={taRef}
        value={local}
        onChange={(e) => update(e.target.value)}
        onKeyDown={onKeyDown}
        onScroll={onScroll}
        onBlur={(e) => flushNow(e.currentTarget.value)}
        spellCheck={false}
        className="vega-editor-ta absolute inset-0 w-full h-full m-0 p-3 bg-transparent text-transparent caret-slate-900 resize-none outline-none whitespace-pre-wrap break-words"
      />
    </div>
  );
}

/* ---------- highlighter ---------------------------------------------------
 * Build the inner HTML of the mirror <pre>. We escape, then wrap matched
 * tokens in <span class="vega-..."> via non-overlapping passes.
 */
const HEADING_RE = /^(\s*)(#{1,6})(\s+)(.*)$/gm;
const TASK_RE    = /!task\b/g;
const AR_RE      = /!AR\b/g;
const USER_RE    = /(^|[\s([])@([a-zA-Z][\w.-]*)/g;
const ATTR_RE    = /#([a-zA-Z][\w-]*)/g;

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}

function highlight(src: string): string {
  // Trailing newline so the mirror grows with the textarea.
  let s = escapeHtml(src.endsWith("\n") ? src : src + "\n");
  // Headings first (line-based, multiline flag).
  s = s.replace(HEADING_RE, (_m, lead, hashes, sp, rest) =>
    `${lead}${hashes}${sp}<span class="vega-heading">${rest}</span>`);
  // !task literal.
  s = s.replace(TASK_RE, '<span class="vega-task">!task</span>');
  // !AR literal (Action Required sub-item under a parent task).
  s = s.replace(AR_RE, '<span class="vega-ar">!AR</span>');
  // @user (preserves the leading char that gated the match).
  s = s.replace(USER_RE, (_m, lead, name) =>
    `${lead}<span class="vega-user">@${name}</span>`);
  // #attr — must NOT eat the # inside our already-emitted spans, so guard
  // by skipping any # that is immediately preceded by a letter/digit (which
  // would already be inside a class name like "vega-heading").
  s = s.replace(ATTR_RE, (m, name, off, full) => {
    const prev = full[off - 1];
    if (prev && /[A-Za-z0-9_-]/.test(prev)) return m;
    return `<span class="vega-attr">#${name}</span>`;
  });
  return s;
}
