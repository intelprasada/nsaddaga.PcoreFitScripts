import { describe, it, expect, beforeEach } from "vitest";
import {
  ALL_FLAVORS,
  FLAVOR_LABEL,
  FLAVOR_PROTOTYPE,
  type EditorFlavor,
} from "../src/components/Editor/types.ts";
import { useEditorPrefs } from "../src/store/editorPrefs.ts";

beforeEach(() => {
  useEditorPrefs.setState({ flavor: "classic" });
});

describe("EditorFlavor metadata", () => {
  it("declares classic + cm6 in a stable order (lexical retired post-bake-off)", () => {
    expect(ALL_FLAVORS).toEqual(["classic", "cm6"]);
  });

  it("provides a label and prototype flag for every flavor", () => {
    for (const f of ALL_FLAVORS) {
      expect(typeof FLAVOR_LABEL[f]).toBe("string");
      expect(FLAVOR_LABEL[f].length).toBeGreaterThan(0);
      expect(typeof FLAVOR_PROTOTYPE[f]).toBe("boolean");
    }
  });

  it("marks CM6 as a prototype; Classic is not", () => {
    expect(FLAVOR_PROTOTYPE.classic).toBe(false);
    expect(FLAVOR_PROTOTYPE.cm6).toBe(true);
  });
});

describe("Flavor selection round-trip", () => {
  // Smoke-checks the pieces the tab strip relies on without bringing in
  // React Testing Library (kept lightweight; component-render coverage is
  // exercised end-to-end by the Playwright suite in #166).
  it("each declared flavor can be set and retrieved", () => {
    const { setFlavor } = useEditorPrefs.getState();
    for (const f of ALL_FLAVORS) {
      setFlavor(f as EditorFlavor);
      expect(useEditorPrefs.getState().flavor).toBe(f);
    }
  });
});
