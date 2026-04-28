/**
 * Client-side gamification opt-out, mirroring the CLI's `vn config gamify=on/off`.
 *
 * Stored in `localStorage` under `veganotes.gamify` ("on" | "off"); defaults
 * to ON when unset. Server keeps recording either way, so flipping back on
 * later restores the user's full history (consistent with Phase 4 design).
 *
 * Subscribers are notified synchronously when the value flips so the
 * `<UnlockToast>` and `<MeView>` can react without a route change.
 */

const KEY = "veganotes.gamify";

type Listener = (enabled: boolean) => void;
const _listeners = new Set<Listener>();

function _readRaw(): string | null {
  try {
    return typeof window !== "undefined" ? window.localStorage.getItem(KEY) : null;
  } catch {
    return null;
  }
}

export function isGamifyEnabled(): boolean {
  const raw = _readRaw();
  // Default ON: only an explicit "off" disables it.
  return raw !== "off";
}

export function setGamifyEnabled(enabled: boolean): void {
  try {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(KEY, enabled ? "on" : "off");
    }
  } catch {
    /* private mode / quota — ignore */
  }
  for (const fn of _listeners) {
    try { fn(enabled); } catch { /* listener errors must not propagate */ }
  }
}

export function subscribeGamify(fn: Listener): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
