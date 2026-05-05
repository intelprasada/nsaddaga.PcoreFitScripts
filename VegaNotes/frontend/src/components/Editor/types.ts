/**
 * Shared contract the editor implements.  Kept as a small interface so
 * App-level draft-buffer / dirty / Ctrl+S plumbing stays decoupled from
 * the concrete CodeMirror integration.  See umbrella issue #162.
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
