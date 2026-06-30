/**
 * P0-task close-celebration trigger logic (#180 — tier-1 burst).
 *
 * Pure helper, no React imports, so it is trivially unit-testable.
 * Used by `<TaskCard>` to decide whether to render the "🎉 Replay"
 * button on an already-closed P0 card. The on-close burst itself is
 * fired from the API client interceptor (api/client.ts) rather than
 * a per-card transition observer, because Kanban re-bucketing
 * unmounts/remounts the card on status change.
 */

/**
 * True iff the "🎉 Replay" button should be offered on a card —
 * the task is currently done, has P0 priority, and gamification is on.
 *
 * Reopens (status flipping away from `done`) hide the button again.
 */
export function shouldShowReplayButton(
  status: string | null | undefined,
  priority: string | null | undefined,
  gamifyEnabled: boolean,
): boolean {
  if (!gamifyEnabled) return false;
  if (status !== "done") return false;
  const prio = (priority ?? "").trim().toUpperCase();
  return prio === "P0";
}
