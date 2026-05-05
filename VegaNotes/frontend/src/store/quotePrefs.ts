import { create } from "zustand";
import { persist } from "zustand/middleware";

interface QuotePrefsState {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
  toggle: () => void;
}

export const useQuotePrefs = create<QuotePrefsState>()(
  persist(
    (set, get) => ({
      enabled: true,
      setEnabled: (enabled) => set({ enabled }),
      toggle: () => set({ enabled: !get().enabled }),
    }),
    {
      name: "vega:quotes:v1",
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (typeof state.enabled !== "boolean") state.enabled = true;
      },
    },
  ),
);
