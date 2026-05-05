import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Editor preferences.  After #162 closed and Classic was retired, the
 * only persisted choice is the Vim keymap on top of CM6 (#168).
 */
interface EditorPrefsState {
  /** Vim keymap on top of CM6 (#168). Off by default. */
  vim: boolean;
  setVim: (v: boolean) => void;
}

export const useEditorPrefs = create<EditorPrefsState>()(
  persist(
    (set) => ({
      vim: false,
      setVim: (vim) => set({ vim }),
    }),
    {
      name: "vega:editor:v1",
      partialize: (state) => ({ vim: state.vim }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as { vim?: unknown };
        return {
          ...current,
          vim: typeof p.vim === "boolean" ? p.vim : false,
        };
      },
    },
  ),
);

