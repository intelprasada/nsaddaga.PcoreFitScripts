import { CM6Editor } from "./CM6Editor";
import { useEditorPrefs } from "../../store/editorPrefs";
import type { EditorHostProps } from "./types";

/**
 * Top-level editor host.  CM6 is now the only editor (#162 closed); the
 * historical flavor tab strip and Classic editor were retired after the
 * bake-off.  Renders just the CM6 editor and a small Vim-keybindings
 * toggle chip above it.
 */
export function NoteEditor(props: EditorHostProps) {
  const vim = useEditorPrefs((s) => s.vim);
  const setVim = useEditorPrefs((s) => s.setVim);
  return (
    <div className="flex flex-col h-full">
      <div className="flex justify-end mb-1">
        <button
          type="button"
          aria-pressed={vim}
          aria-label={`Vim keybindings ${vim ? "on" : "off"}`}
          title={`Vim keybindings ${vim ? "on" : "off"}`}
          onClick={() => setVim(!vim)}
          className={`px-2 py-0.5 text-xs rounded border ${
            vim
              ? "bg-emerald-600 text-white border-emerald-600"
              : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"
          }`}
        >
          Vim
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <CM6Editor {...props} />
      </div>
    </div>
  );
}

