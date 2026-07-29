/**
 * Parse a badge progress string emitted by the backend ("0/1", "5/13",
 * "3/10 day(s)") into a clamped 0–1 ratio for the progress bar plus the
 * original human-readable label.
 *
 * Previously BadgeGrid fed the raw string straight into
 * ``Math.min(1, progress)`` which coerced "0/1" → NaN and rendered a literal
 * "NaN%" on every locked badge (#329). Keeping this pure + separate makes it
 * unit-testable and impossible to regress silently.
 */
export interface BadgeProgress {
  ratio: number; // 0..1, always finite
  label: string; // e.g. "5/13 day(s)" — safe to render verbatim
}

export function parseBadgeProgress(raw: string | number | null | undefined): BadgeProgress | null {
  if (raw == null) return null;

  // Back-compat: if a bare number ever arrives, treat it as an already-ratio.
  if (typeof raw === "number") {
    return Number.isFinite(raw) ? { ratio: clamp01(raw), label: `${Math.round(clamp01(raw) * 100)}%` } : null;
  }

  const label = raw.trim();
  if (label === "") return null;

  const m = label.match(/(\d+)\s*\/\s*(\d+)/);
  if (m) {
    const num = Number(m[1]);
    const den = Number(m[2]);
    const ratio = den > 0 ? clamp01(num / den) : 0;
    return { ratio, label };
  }

  // A single number with no denominator — can't compute a ratio; show 0-width
  // bar but keep the label so the user still sees the raw value.
  const single = label.match(/^(\d+(?:\.\d+)?)$/);
  if (single) {
    const val = Number(single[1]);
    // A 0..1 float reads as a ratio; anything larger has no scale, so 0.
    const ratio = val <= 1 ? clamp01(val) : 0;
    return { ratio, label };
  }

  return { ratio: 0, label };
}

export function clamp01(x: number): number {
  if (!Number.isFinite(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}
