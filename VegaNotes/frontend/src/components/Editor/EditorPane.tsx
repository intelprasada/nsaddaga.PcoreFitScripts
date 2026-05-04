import { useEditorPrefs } from "../../store/editorPrefs";
import { CM6Editor } from "./CM6Editor";
import { ClassicEditor } from "./ClassicEditor";
import { EditorFlavorTabs } from "./EditorFlavorTabs";
import type { EditorFlavor, EditorHostProps } from "./types";

const FLAVORS: Record<EditorFlavor, React.FC<EditorHostProps>> = {
  classic: ClassicEditor,
  cm6: CM6Editor,
};

/**
 * Top-level editor host: renders the flavor tab strip and mounts the
 * currently-selected editor.  Switching tabs unmounts the previous flavor
 * and mounts the next; the parent App owns the draft buffer (`value`),
 * so no edits are lost across switches.
 *
 * Public API matches `EditorHostProps` so swapping `<NoteEditor>` for
 * `<EditorPane>` is a drop-in change.
 */
export function EditorPane(props: EditorHostProps) {
  const flavor = useEditorPrefs((s) => s.flavor);
  const Active = FLAVORS[flavor] ?? ClassicEditor;
  return (
    <div className="flex flex-col h-full">
      <EditorFlavorTabs />
      <div
        id={`vega-editor-panel-${flavor}`}
        role="tabpanel"
        className="flex-1 min-h-0"
      >
        <Active {...props} />
      </div>
    </div>
  );
}
