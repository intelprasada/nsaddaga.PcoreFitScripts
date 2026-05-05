import asicDv from "./asic-dv.json";
import classic from "./classic.json";

export interface Quote {
  id: string;
  text: string;
  attribution: string;
  culture: string;
  original?: string;
}

export type ThemeKey = "asic-dv" | "classic";

export const QUOTE_THEMES: Record<ThemeKey, { label: string; quotes: Quote[] }> = {
  "asic-dv": { label: "ASIC Design Verification", quotes: asicDv as Quote[] },
  "classic": { label: "World Cultures (classic)",  quotes: classic as Quote[] },
};

export const DEFAULT_THEME: ThemeKey = "asic-dv";
