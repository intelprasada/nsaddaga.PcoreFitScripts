import { describe, it, expect, beforeEach } from "vitest";
import { useEditorPrefs } from "../src/store/editorPrefs.ts";

beforeEach(() => {
  useEditorPrefs.setState({ flavor: "classic" });
  try {
    localStorage.removeItem("vega:editor:v1");
  } catch {
    /* jsdom may not expose localStorage */
  }
});

describe("useEditorPrefs", () => {
  it("defaults to classic", () => {
    expect(useEditorPrefs.getState().flavor).toBe("classic");
  });

  it("setFlavor updates the store", () => {
    useEditorPrefs.getState().setFlavor("cm6");
    expect(useEditorPrefs.getState().flavor).toBe("cm6");
    useEditorPrefs.getState().setFlavor("lexical");
    expect(useEditorPrefs.getState().flavor).toBe("lexical");
  });

  it("persists the selection to localStorage under vega:editor:v1", () => {
    useEditorPrefs.getState().setFlavor("cm6");
    const raw = localStorage.getItem("vega:editor:v1");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    // zustand persist v4 wraps state in { state, version }.
    expect(parsed.state.flavor).toBe("cm6");
  });

  it("falls back to classic if persisted value is unrecognised", () => {
    // Simulate an old build that wrote a now-removed flavor.
    localStorage.setItem(
      "vega:editor:v1",
      JSON.stringify({ state: { flavor: "monaco" }, version: 0 }),
    );
    // Force a re-hydration by re-importing-ish: just call rehydrate().
    useEditorPrefs.persist.rehydrate();
    expect(useEditorPrefs.getState().flavor).toBe("classic");
  });
});
