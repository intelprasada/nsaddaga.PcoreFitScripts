import { describe, it, expect, beforeEach } from "vitest";
import { useQuotePrefs } from "../src/store/quotePrefs";

describe("quotePrefs store", () => {
  beforeEach(() => {
    localStorage.clear();
    useQuotePrefs.setState({ enabled: true });
  });

  it("defaults to enabled", () => {
    expect(useQuotePrefs.getState().enabled).toBe(true);
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
});
