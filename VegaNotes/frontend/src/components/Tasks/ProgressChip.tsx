/**
 * #320: recurring #progress metric chip.
 *
 * Renders as a compact capsule showing `N/D · P%` (or just `N` for a
 * bare counter) with a color band derived from the percent complete.
 * When history is available a tiny sparkline of the last few readings
 * is rendered inline next to the numeric label.
 */
import type { Task } from "../../api/client";
import type { ProgressHistoryRow } from "../../api/client";
import {
  formatProgressChip,
  formatProgressPercent,
  parseProgressValue,
  progressColor,
  PROGRESS_COLOR_CLASS,
  sparklinePoints,
} from "../../lib/progressChip";

interface Props {
  task: Task;
  /** Optional pre-fetched history for the sparkline. */
  history?: ProgressHistoryRow[] | null;
}

/**
 * Read the current `#progress` value from the task attrs.  Present as
 * a single scalar; the parser stores this token as `multi: false`.
 */
function currentProgress(task: Task): string | null {
  const raw = task.attrs?.progress;
  if (raw == null) return null;
  if (Array.isArray(raw)) return raw[0] ?? null;
  return String(raw);
}

export function ProgressChip({ task, history }: Props) {
  const raw = currentProgress(task);
  if (!raw) return null;
  const p = parseProgressValue(raw);
  if (!p) return null;

  const color = progressColor(p);
  const numeric = formatProgressChip(p);
  const pct = formatProgressPercent(p);

  // Sparkline: use percent history where available, falling back to the
  // raw numerator for bare counters.  Cap at last 8 samples so the chip
  // stays narrow on dense cards.
  const samples =
    history && history.length > 0
      ? history
          .map((r) =>
            r.denominator && r.denominator > 0
              ? Math.round((r.numerator / r.denominator) * 100)
              : r.numerator,
          )
          .slice(-8)
      : [];

  const showSpark = samples.length >= 2;
  const points = showSpark ? sparklinePoints(samples, 32, 10) : "";

  const tooltipParts = [
    `Progress: ${numeric}${pct ? ` (${pct})` : ""}`,
    p.label ? `Label: ${p.label}` : null,
    history && history.length > 0
      ? `History: ${history.length} week${history.length === 1 ? "" : "s"}`
      : null,
  ].filter(Boolean);

  return (
    <span
      className={
        "chip inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 " +
        "text-xs font-mono " +
        PROGRESS_COLOR_CLASS[color]
      }
      title={tooltipParts.join(" · ")}
      data-testid="progress-chip"
    >
      <span aria-hidden="true" className="opacity-70">↻</span>
      <span>{numeric}</span>
      {pct !== null && <span className="opacity-70">· {pct}</span>}
      {p.label && (
        <span className="opacity-60 uppercase text-[10px]">{p.label}</span>
      )}
      {showSpark && (
        <svg
          width={32}
          height={10}
          viewBox={`0 0 32 10`}
          className="opacity-70"
          aria-hidden="true"
        >
          <polyline
            points={points}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.25}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        </svg>
      )}
    </span>
  );
}
