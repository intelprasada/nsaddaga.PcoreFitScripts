/**
 * P0-task close-celebration trigger logic (#180 — tier-1 burst).
 *
 * Pure helper, no React imports, so it is trivially unit-testable.
 * Used by `<TaskCard>` to decide whether a status transition should
 * fire a confetti burst, and by the "Replay" button shown on already
 * closed P0 cards.
 */

export type GamifyReader = () => boolean;

/**
 * Decide whether a status transition warrants a P0 celebration burst.
 *
 * Fires only when ALL of these are true:
 *   - previous status was not `done` (initial mount with status==='done'
 *     is treated as "no transition" by passing prevStatus = null/undefined)
 *   - next status is exactly `done`
 *   - `priority` is exactly `"P0"` (case-insensitive)
 *   - gamification is enabled (`gamifyEnabled === true`)
 */
export function shouldFireP0Burst(
  prevStatus: string | null | undefined,
  nextStatus: string | null | undefined,
  priority: string | null | undefined,
  gamifyEnabled: boolean,
): boolean {
  if (!gamifyEnabled) return false;
  if (prevStatus == null) return false;
  if (prevStatus === "done") return false;
  if (nextStatus !== "done") return false;
  const prio = (priority ?? "").trim().toUpperCase();
  return prio === "P0";
}

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
