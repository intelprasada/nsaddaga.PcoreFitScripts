// AR status utilities.
//
// Extracted from TaskEditPopover / MyTasksView so the cycle order and
// pill colors stay consistent everywhere ARs are rendered, and so the
// cycle can be unit-tested without mounting the popover.

/** Status cycle order used by AR row pills. Matches the standard task
 * status set; the cycle skips unknown statuses. */
export const AR_STATUS_CYCLE = ["todo", "in-progress", "blocked", "done"] as const;

export type ArStatus = (typeof AR_STATUS_CYCLE)[number];

/** Advance an AR's status one step forward in the cycle, wrapping around.
 * Unknown statuses fall through to the first known status so the button
 * still makes progress instead of no-op'ing. */
export function nextArStatus(current: string): string {
  const i = AR_STATUS_CYCLE.indexOf(current as ArStatus);
  if (i === -1) return AR_STATUS_CYCLE[0];
  return AR_STATUS_CYCLE[(i + 1) % AR_STATUS_CYCLE.length];
}

/** Tailwind classes for each AR status pill.
 * `default` is the fallback for unknown / custom statuses. */
export const AR_STATUS_STYLES: Record<string, string> = {
  "todo":        "bg-slate-100 text-slate-700 border-slate-300",
  "in-progress": "bg-sky-100 text-sky-800 border-sky-300",
  "blocked":     "bg-rose-100 text-rose-800 border-rose-300",
  "done":        "bg-emerald-100 text-emerald-800 border-emerald-300",
  "default":     "bg-slate-100 text-slate-600 border-slate-200",
};
