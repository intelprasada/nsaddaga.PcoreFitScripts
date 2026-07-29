/**
 * Inline field validators for the task edit popover (#329).
 *
 * These mirror the backend PATCH rules exactly so the UI never blocks input
 * the server would accept, nor accepts input it would reject:
 *   - progress: ^\d+(?:/\d+)?(?:\s+[A-Za-z][\w-]*)?$  (denominator > 0)
 *   - hsd / jira / pr: each comma-separated item must be whitespace-free
 * Empty is always valid (it clears the token). Returns a short message to show
 * under the field, or null when valid.
 */

const PROGRESS_RE = /^\d+(?:\/\d+)?(?:\s+[A-Za-z][\w-]*)?$/;

export function validateProgress(value: string): string | null {
  const p = value.trim();
  if (p === "") return null;
  if (!PROGRESS_RE.test(p)) return "Use N, N/D, or N/D label (e.g. 30/54 fixed).";
  const m = p.match(/^(\d+)(?:\/(\d+))?/);
  if (m && m[2] !== undefined && Number(m[2]) === 0) return "Denominator must be greater than 0.";
  return null;
}

/**
 * HSD / JIRA / PR are comma-separated lists whose individual items may not
 * contain whitespace (the backend rejects internal spaces for these keys).
 */
export function validateNoSpaceCsv(value: string, label: string): string | null {
  const items = value.split(",").map((s) => s.trim()).filter(Boolean);
  for (const it of items) {
    if (/\s/.test(it)) return `${label} values can't contain spaces — separate multiple with commas.`;
  }
  return null;
}
