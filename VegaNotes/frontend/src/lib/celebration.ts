/**
 * Global celebration event bus (#180 — tier-1 close burst).
 *
 * Sibling pattern to `onAwardedBadges` in `api/client.ts`. Any write
 * path can publish a celebration; a single `<CelebrationOverlay>` at
 * the app root subscribes and renders the particle burst into a
 * full-viewport portal.
 *
 * Pure module (no React imports) — trivially unit-testable.
 */

export interface CelebrationOrigin {
  x: number;
  y: number;
}

export interface CelebrationEvent {
  /** Optional pixel origin (viewport coordinates). If omitted, the
   *  overlay falls back to the viewport center. */
  origin?: CelebrationOrigin;
  /** Priority string, used purely for telemetry/styling. */
  priority?: string;
  /** Stable identity for dedup/coalesce (typically a task UUID). */
  sourceId?: string;
}

type Listener = (event: CelebrationEvent) => void;

const _listeners = new Set<Listener>();

export function onCelebration(fn: Listener): () => void {
  _listeners.add(fn);
  return () => {
    _listeners.delete(fn);
  };
}

export function triggerCelebration(event: CelebrationEvent = {}): void {
  for (const fn of _listeners) {
    try {
      fn(event);
    } catch {
      // Listener errors must never break the caller (often an API response handler).
    }
  }
}

/**
 * Test-only — clear all subscribers. Not exported via index by intent;
 * imported directly by tests.
 */
export function _resetCelebrationListenersForTests(): void {
  _listeners.clear();
}
