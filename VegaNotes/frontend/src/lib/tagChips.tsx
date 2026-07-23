// Extract "extra" hashtag attrs from a task and render them as chips.
//
// A task's `attrs` map can contain any key. Reserved keys are surfaced
// through their own dedicated UI (Priority/Eta/Owner chips, Project/
// Feature lists, Note popover, etc.), so this helper skips them and
// emits `#{key}` / `#{key}=value` chips for the remainder.
//
// Introduced for issue #275 — bare hashtags on task-attached lines are
// now indexed as presence attributes and need a visual affordance.

import type { Task } from "../api/client";

const HIDDEN_KEYS = new Set<string>([
  // Surfaced elsewhere (chip UI or dedicated columns).
  "id", "priority", "eta", "owner", "project", "feature",
  "status", "task", "ar", "link", "estimate", "note",
  // Free-form scheduling attrs already shown as part of eta or notes.
  "estimate_units",
  // #314 / #316 external-link tokens: rendered as their own clickable
  // capsule via <LinkChips />, so we skip them here to avoid a second
  // ugly `#url=[Label](https://…)` tag chip on the same card.
  "url", "hsd", "jira", "pr",
]);

export interface TagChip {
  key: string;
  /** Empty string means bare `#key` (presence tag). */
  value: string;
  /** Stable react key. */
  reactKey: string;
}

/** Compute the set of extra-tag chips to render for a task. */
export function extraTagChips(task: Task): TagChip[] {
  const out: TagChip[] = [];
  const attrs = task.attrs ?? {};
  for (const [key, raw] of Object.entries(attrs)) {
    if (HIDDEN_KEYS.has(key.toLowerCase())) continue;
    const values = Array.isArray(raw) ? raw : [raw];
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      const s = v == null ? "" : String(v);
      out.push({ key, value: s, reactKey: `${key}::${i}::${s}` });
    }
  }
  return out;
}

/** Inline chip renderer. Skips itself entirely when there are no extras. */
export function TagChips({ task, size = "sm" }: { task: Task; size?: "sm" | "xs" }) {
  const chips = extraTagChips(task);
  if (chips.length === 0) return null;
  const cls =
    size === "xs"
      ? "chip chip-tag text-[10px] py-0 px-1.5"
      : "chip chip-tag";
  return (
    <>
      {chips.map((c) => (
        <span
          key={c.reactKey}
          className={cls}
          title={c.value ? `${c.key} = ${c.value}` : `Tag: #${c.key}`}
        >
          #{c.key}
          {c.value ? <span className="opacity-60">={c.value}</span> : null}
        </span>
      ))}
    </>
  );
}
