import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_THEME, QUOTE_THEMES, type ThemeKey } from "../data/quotes";

interface QuotePrefsState {
  enabled: boolean;
  theme: ThemeKey;
  setEnabled: (v: boolean) => void;
  setTheme: (t: ThemeKey) => void;
  toggle: () => void;
}

function isTheme(v: unknown): v is ThemeKey {
  return typeof v === "string" && v in QUOTE_THEMES;
}

export const useQuotePrefs = create<QuotePrefsState>()(
  persist(
    (set, get) => ({
      enabled: true,
      theme: DEFAULT_THEME,
      setEnabled: (enabled) => set({ enabled }),
      setTheme: (theme) => set({ theme }),
      toggle: () => set({ enabled: !get().enabled }),
    }),
    {
      name: "vega:quotes:v1",
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (typeof state.enabled !== "boolean") state.enabled = true;
        if (!isTheme(state.theme)) state.theme = DEFAULT_THEME;
      },
    },
  ),
);

