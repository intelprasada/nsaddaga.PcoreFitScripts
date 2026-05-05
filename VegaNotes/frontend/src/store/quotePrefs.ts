import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_THEME, QUOTE_THEMES, type Quote, type ThemeKey } from "../data/quotes";

interface QuotePrefsState {
  enabled: boolean;
  theme: ThemeKey;
  customQuotes: Quote[];
  setEnabled: (v: boolean) => void;
  setTheme: (t: ThemeKey) => void;
  toggle: () => void;
  addCustomQuote: (text: string, attribution?: string, source?: string) => void;
  removeCustomQuote: (id: string) => void;
}

function isTheme(v: unknown): v is ThemeKey {
  return typeof v === "string" && v in QUOTE_THEMES;
}

function isQuoteArray(v: unknown): v is Quote[] {
  return Array.isArray(v) && v.every((q) =>
    q && typeof q === "object" &&
    typeof (q as Quote).id === "string" &&
    typeof (q as Quote).text === "string");
}

function newCustomId(): string {
  return `custom-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export const useQuotePrefs = create<QuotePrefsState>()(
  persist(
    (set, get) => ({
      enabled: true,
      theme: DEFAULT_THEME,
      customQuotes: [],
      setEnabled: (enabled) => set({ enabled }),
      setTheme: (theme) => set({ theme }),
      toggle: () => set({ enabled: !get().enabled }),
      addCustomQuote: (text, attribution, source) => {
        const trimmed = text.trim();
        if (!trimmed) return;
        const q: Quote = {
          id: newCustomId(),
          text: trimmed,
          attribution: (attribution ?? "").trim() || "You",
          culture: (source ?? "").trim() || "Personal",
        };
        set({ customQuotes: [...get().customQuotes, q] });
      },
      removeCustomQuote: (id) => {
        set({ customQuotes: get().customQuotes.filter((q) => q.id !== id) });
      },
    }),
    {
      name: "vega:quotes:v1",
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        if (typeof state.enabled !== "boolean") state.enabled = true;
        if (!isTheme(state.theme)) state.theme = DEFAULT_THEME;
        if (!isQuoteArray(state.customQuotes)) state.customQuotes = [];
      },
    },
  ),
);


