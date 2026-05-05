import { describe, it, expect, beforeEach } from "vitest";
import { useQuotePrefs } from "../src/store/quotePrefs";
import { DEFAULT_THEME, QUOTE_THEMES } from "../src/data/quotes";

describe("quotePrefs store", () => {
  beforeEach(() => {
    localStorage.clear();
    useQuotePrefs.setState({ enabled: true, theme: DEFAULT_THEME });
  });

  it("defaults to enabled", () => {
    expect(useQuotePrefs.getState().enabled).toBe(true);
  });

  it("default theme is asic-dv", () => {
    expect(useQuotePrefs.getState().theme).toBe("asic-dv");
  });

  it("toggle flips the flag", () => {
    useQuotePrefs.getState().toggle();
    expect(useQuotePrefs.getState().enabled).toBe(false);
    useQuotePrefs.getState().toggle();
    expect(useQuotePrefs.getState().enabled).toBe(true);
  });

  it("setEnabled persists explicit value", () => {
    useQuotePrefs.getState().setEnabled(false);
    expect(useQuotePrefs.getState().enabled).toBe(false);
  });

  it("setTheme switches theme", () => {
    useQuotePrefs.getState().setTheme("classic");
    expect(useQuotePrefs.getState().theme).toBe("classic");
  });

  it("registry exposes both themes with non-empty quote lists", () => {
    expect(QUOTE_THEMES["asic-dv"].quotes.length).toBeGreaterThan(0);
    expect(QUOTE_THEMES["classic"].quotes.length).toBeGreaterThan(0);
  });
});
