/**
 * Shared contract every editor flavor (Classic / CM6 / Lexical) implements.
 * Lets `EditorPane.tsx` swap implementations without touching App-level
 * draft-buffer / dirty / Ctrl+S plumbing.  See umbrella issue #162 / #163.
 */
export interface EditorHostProps {
  value: string;
  onChange: (next: string) => void;
  readOnly?: boolean;
  /** Sidebar / My-Tasks click-to-jump hint (1-based source line). */
  gotoLine?: number;
  /** Optional — flavor reports its dirty state for the App-level ● indicator. */
  onDirtyChange?: (dirty: boolean) => void;
  /** Optional — flavor calls this on Ctrl+S; App.tsx wires it to flushSave. */
  requestSave?: () => void;
}

export type EditorFlavor = "classic" | "cm6";

export const ALL_FLAVORS: EditorFlavor[] = ["classic", "cm6"];

export const FLAVOR_LABEL: Record<EditorFlavor, string> = {
  classic: "Classic",
  cm6: "CM6",
};

/** Prototype tabs are badged so users know they're experimental. */
export const FLAVOR_PROTOTYPE: Record<EditorFlavor, boolean> = {
  classic: false,
  cm6: true,
};
