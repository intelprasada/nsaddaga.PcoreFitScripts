import { describe, it, expect, beforeEach } from "vitest";
import { useEditorPrefs } from "../src/store/editorPrefs.ts";

beforeEach(() => {
  useEditorPrefs.setState({ vim: false });
  try {
    localStorage.removeItem("vega:editor:v1");
  } catch {
    /* jsdom may not expose localStorage */
  }
});

describe("useEditorPrefs", () => {
  it("defaults to vim off (CM6 is the only editor post-#162)", () => {
    expect(useEditorPrefs.getState().vim).toBe(false);
  });

  it("setVim toggles the vim flag", () => {
    useEditorPrefs.getState().setVim(true);
    expect(useEditorPrefs.getState().vim).toBe(true);
    useEditorPrefs.getState().setVim(false);
    expect(useEditorPrefs.getState().vim).toBe(false);
  });

  it("persists the vim flag to localStorage under vega:editor:v1", () => {
    useEditorPrefs.getState().setVim(true);
    const raw = localStorage.getItem("vega:editor:v1");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.vim).toBe(true);
  });

  it("coerces non-boolean persisted vim values to false", () => {
    localStorage.setItem(
      "vega:editor:v1",
      JSON.stringify({ state: { vim: "yes" }, version: 0 }),
    );
    useEditorPrefs.persist.rehydrate();
    expect(useEditorPrefs.getState().vim).toBe(false);
  });

  it("ignores stale legacy 'flavor' field from old persisted state", () => {
    localStorage.setItem(
      "vega:editor:v1",
      JSON.stringify({ state: { flavor: "classic", vim: true }, version: 0 }),
    );
    useEditorPrefs.persist.rehydrate();
    // vim survives because it's still a valid boolean; flavor is ignored.
    expect(useEditorPrefs.getState().vim).toBe(true);
    expect((useEditorPrefs.getState() as { flavor?: string }).flavor).toBeUndefined();
  });
});

