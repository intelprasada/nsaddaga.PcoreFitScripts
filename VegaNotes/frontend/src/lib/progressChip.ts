/**
 * #320: helpers for rendering the recurring `#progress` metric chip.
 *
 * The `#progress` attribute stores one of:
 *   - `N`              — bare counter (no denominator, always shows the
 *                        counter blue).
 *   - `N/D`            — ratio; percent drives the color band.
 *   - `N/D label`      — ratio with a single-word label (e.g. `fixed`).
 *
 * Everything here is pure — the `<ProgressChip>` component and the
 * popover history section import these helpers.
 */

export interface ParsedProgress {
  numerator: number;
  denominator: number | null;
  label: string | null;
  /** Percentage (0-100+) when a denominator is present, otherwise null. */
  percent: number | null;
}

/** Color band derived from the percent (or the counter-blue for bare N). */
export type ProgressColor = "red" | "amber" | "green" | "gold" | "blue";

const RATIO_RE = /^\s*(\d+)(?:\/(\d+))?(?:\s+([A-Za-z][\w-]*))?\s*$/;

/** Parse a stored `#progress` value; returns null for garbage. */
export function parseProgressValue(raw: string): ParsedProgress | null {
  if (!raw) return null;
  const m = raw.match(RATIO_RE);
  if (!m) return null;
  const numerator = Number(m[1]);
  const denominator = m[2] ? Number(m[2]) : null;
  const label = m[3] ?? null;
  if (!Number.isFinite(numerator)) return null;
  if (denominator !== null && (!Number.isFinite(denominator) || denominator <= 0)) {
    return null;
  }
  const percent =
    denominator !== null && denominator > 0
      ? Math.round((numerator / denominator) * 100)
      : null;
  return { numerator, denominator, label, percent };
}

/**
 * Map a parsed progress reading to its color band.  Thresholds:
 *   - counter-only (no denom)  → blue
 *   - < 25 %                   → red
 *   - 25 – 74 %                → amber
 *   - 75 – 99 %                → green
 *   - ≥ 100 %                  → gold  (over-achieved / bucket cleared)
 */
export function progressColor(p: ParsedProgress): ProgressColor {
  if (p.percent === null) return "blue";
  if (p.percent >= 100) return "gold";
  if (p.percent >= 75) return "green";
  if (p.percent >= 25) return "amber";
  return "red";
}

/** Tailwind classes for each color band. */
export const PROGRESS_COLOR_CLASS: Record<ProgressColor, string> = {
  red:   "bg-red-50 text-red-700 border-red-300",
  amber: "bg-amber-50 text-amber-700 border-amber-300",
  green: "bg-emerald-50 text-emerald-700 border-emerald-300",
  gold:  "bg-yellow-50 text-yellow-700 border-yellow-300",
  blue:  "bg-sky-50 text-sky-700 border-sky-300",
};

/** Compact label shown inside the chip. */
export function formatProgressChip(p: ParsedProgress): string {
  if (p.denominator === null) return String(p.numerator);
  return `${p.numerator}/${p.denominator}`;
}

/** Optional trailing percent suffix (only when a denominator is present). */
export function formatProgressPercent(p: ParsedProgress): string | null {
  if (p.percent === null) return null;
  return `${p.percent}%`;
}

/**
 * Build the SVG polyline points for a sparkline of `values` inside a
 * `width × height` box.  Values are the *percent* readings; the
 * baseline is 0 and the ceiling is `max(100, ...values)` so a spike
 * past 100 % (over-achieved) still fits on the sparkline without
 * flattening the rest of the trend.
 */
export function sparklinePoints(
  values: number[],
  width: number,
  height: number,
): string {
  if (values.length === 0) return "";
  if (values.length === 1) {
    // Single reading: draw a flat dash across the middle.
    const y = height / 2;
    return `0,${y} ${width},${y}`;
  }
  const ceiling = Math.max(100, ...values);
  const step = width / (values.length - 1);
  return values
    .map((v, i) => {
      const x = i * step;
      const y = height - (v / ceiling) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * Classify a history sample vs its predecessor: improving, regressed
 * (denominator grew faster than numerator kept up), or steady.  Used
 * by the popover history table's arrow indicator.
 */
export type Trend = "up" | "down" | "flat";

export function trendBetween(
  prev: ParsedProgress | null,
  cur: ParsedProgress,
): Trend {
  if (!prev) return "flat";
  const prevPct = prev.percent ?? prev.numerator;
  const curPct = cur.percent ?? cur.numerator;
  if (curPct > prevPct) return "up";
  if (curPct < prevPct) return "down";
  return "flat";
}
