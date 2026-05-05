import { describe, it, expect, beforeEach } from "vitest";
import { useQuotePrefs } from "../src/store/quotePrefs";
import { DEFAULT_THEME, QUOTE_THEMES } from "../src/data/quotes";

describe("quotePrefs store", () => {
  beforeEach(() => {
    localStorage.clear();
    useQuotePrefs.setState({ enabled: true, theme: DEFAULT_THEME, customQuotes: [] });
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

  it("addCustomQuote appends with defaults and removeCustomQuote removes", () => {
    useQuotePrefs.getState().addCustomQuote("Hello world");
    let list = useQuotePrefs.getState().customQuotes;
    expect(list).toHaveLength(1);
    expect(list[0].text).toBe("Hello world");
    expect(list[0].attribution).toBe("You");
    expect(list[0].culture).toBe("Personal");
    expect(list[0].id.startsWith("custom-")).toBe(true);

    useQuotePrefs.getState().addCustomQuote("Stay focused.", "Marcus", "Meditations");
    list = useQuotePrefs.getState().customQuotes;
    expect(list).toHaveLength(2);
    expect(list[1].attribution).toBe("Marcus");
    expect(list[1].culture).toBe("Meditations");

    const idToRemove = list[0].id;
    useQuotePrefs.getState().removeCustomQuote(idToRemove);
    expect(useQuotePrefs.getState().customQuotes).toHaveLength(1);
    expect(useQuotePrefs.getState().customQuotes[0].text).toBe("Stay focused.");
  });

  it("addCustomQuote ignores blank input", () => {
    useQuotePrefs.getState().addCustomQuote("   ");
    expect(useQuotePrefs.getState().customQuotes).toHaveLength(0);
  });
});
