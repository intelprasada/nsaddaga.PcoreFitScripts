import type { EditorHostProps } from "./types";

/**
 * Lexical editor — prototype tab.  Implementation lands in #165.
 * Until then, render a clearly-labelled stub that still honors `value`
 * (read-only preview) so the user can see they switched but can't edit.
 */
export function LexicalEditor({ value }: EditorHostProps) {
  return (
    <div className="vega-editor-stub w-full h-[28rem] rounded border bg-slate-50 p-4 text-sm text-slate-600 overflow-auto">
      <p className="mb-3">
        <strong>Lexical editor</strong> — coming in{" "}
        <a
          className="text-sky-600 hover:underline"
          href="https://github.com/intelprasada/nsaddaga.PcoreFitScripts/issues/165"
          target="_blank"
          rel="noreferrer"
        >
          issue #165
        </a>
        . Switch back to <em>Classic</em> to edit.
      </p>
      <pre className="whitespace-pre-wrap text-xs text-slate-500 border-t pt-2">
        {value}
      </pre>
    </div>
  );
}
