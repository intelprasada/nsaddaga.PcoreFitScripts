import { create } from "zustand";
import { persist } from "zustand/middleware";
import { ALL_FLAVORS, type EditorFlavor } from "../components/Editor/types";

interface EditorPrefsState {
  flavor: EditorFlavor;
  setFlavor: (f: EditorFlavor) => void;
}

function isFlavor(v: unknown): v is EditorFlavor {
  return typeof v === "string" && (ALL_FLAVORS as string[]).includes(v);
}

export const useEditorPrefs = create<EditorPrefsState>()(
  persist(
    (set) => ({
      flavor: "classic",
      setFlavor: (flavor) => set({ flavor }),
    }),
    {
      name: "vega:editor:v1",
      // Defensive: if a future build removes a flavor, fall back to classic
      // instead of crashing the editor pane on first mount.
      onRehydrateStorage: () => (state) => {
        if (state && !isFlavor(state.flavor)) state.flavor = "classic";
      },
    },
  ),
);
