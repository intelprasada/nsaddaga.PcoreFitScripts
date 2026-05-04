/**
 * Backwards-compatibility shim: keep the historical `NoteEditor` import
 * path working while the editor swap (#162) is in progress.  All real
 * mounting now happens via `EditorPane`, which renders the flavor tab
 * strip and delegates to the selected implementation.
 */
export { EditorPane as NoteEditor } from "./EditorPane";
