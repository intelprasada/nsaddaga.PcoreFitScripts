import { useEditorPrefs } from "../../store/editorPrefs";
import {
  ALL_FLAVORS,
  FLAVOR_LABEL,
  FLAVOR_PROTOTYPE,
  type EditorFlavor,
} from "./types";

interface Props {
  /** Optional override; defaults to the persisted store value. */
  value?: EditorFlavor;
  onChange?: (next: EditorFlavor) => void;
}

/**
 * Tab strip above the editor that selects which flavor (Classic / CM6 /
 * Lexical) is mounted.  Keyed off `useEditorPrefs` so the choice persists
 * across reloads.  See umbrella issue #162.
 */
export function EditorFlavorTabs({ value, onChange }: Props = {}) {
  const stored = useEditorPrefs((s) => s.flavor);
  const setStored = useEditorPrefs((s) => s.setFlavor);
  const current = value ?? stored;
  const select = (f: EditorFlavor) => {
    setStored(f);
    onChange?.(f);
  };
  return (
    <div role="tablist" aria-label="Editor flavor" className="flex gap-1 mb-1">
      {ALL_FLAVORS.map((f) => {
        const active = f === current;
        return (
          <button
            key={f}
            role="tab"
            aria-selected={active}
            aria-controls={`vega-editor-panel-${f}`}
            onClick={() => select(f)}
            className={`px-2 py-0.5 text-xs rounded border ${
              active
                ? "bg-sky-600 text-white border-sky-600"
                : "bg-white text-slate-700 border-slate-300 hover:bg-slate-100"
            }`}
            title={
              FLAVOR_PROTOTYPE[f]
                ? `${FLAVOR_LABEL[f]} (prototype)`
                : FLAVOR_LABEL[f]
            }
          >
            {FLAVOR_LABEL[f]}
            {FLAVOR_PROTOTYPE[f] && (
              <span
                className={`ml-1 text-[10px] uppercase ${
                  active ? "text-sky-100" : "text-amber-600"
                }`}
              >
                proto
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
