import { useLayoutEffect, useRef } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
}

/**
 * VegaNotes markdown editor.
 *
 * Implementation: a transparent <textarea> stacked exactly on top of a
 * syntax-highlighted <pre> mirror. The user types into the textarea (so
 * caret, IME, copy/paste, undo all behave natively) and we keep the
 * highlighted layer in lockstep on every change. This pattern (a la CodeJar
 * / Hyperterm-ish) sidesteps every ContentEditable / ProseMirror gotcha
 * around code blocks, decorations and Tailwind Typography.
 *
 * Token coloring matches the rest of the UI:
 *   - `!task`  emerald
 *   - `#attr`  sky
 *   - `@user`  violet
 *   - `# ...`  slate-bold heading
 */
export function NoteEditor({ value, onChange }: Props) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);

  // Keep the mirrored <pre> in sync with the textarea content.
  useLayoutEffect(() => {
    if (preRef.current) preRef.current.innerHTML = highlight(value);
  }, [value]);

  // Tab inserts a literal tab; Shift-Tab outdents one tab/4-space.
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
      onChange(next);
      requestAnimationFrame(() => ta.setSelectionRange(s - drop, en - drop));
    } else {
      const next = v.slice(0, s) + "\t" + v.slice(en);
      onChange(next);
      requestAnimationFrame(() => ta.setSelectionRange(s + 1, s + 1));
    }
  }

  // Mirror scroll position so the highlight overlay stays aligned.
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
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onScroll={onScroll}
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
