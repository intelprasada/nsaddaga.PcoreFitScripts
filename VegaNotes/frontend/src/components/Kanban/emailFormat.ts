import type { Task } from "../../api/client";
import type { FilterState } from "../../store/ui";

export const STATUS_LABELS: Record<string, string> = {
  "todo": "TODO",
  "in-progress": "IN-PROGRESS",
  "blocked": "BLOCKED",
  "done": "DONE",
};

export const PRIORITY_LABELS: Record<number, string> = {
  1: "P1",
  2: "P2",
  3: "P3",
  4: "P4",
  5: "P5",
};

export interface EmailRecipients {
  /** Owner tokens that look like email addresses (deduped, lowercase). */
  resolved: string[];
  /** Owner tokens that don't look like emails — flagged for the user. */
  unresolved: string[];
}

const EMAIL_RE = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

export function looksLikeEmail(s: string): boolean {
  return EMAIL_RE.test(s.trim());
}

/** Partition a list of owner tokens into email-shaped vs not-yet-resolvable. */
export function partitionOwners(tokens: Iterable<string>): EmailRecipients {
  const resolved = new Set<string>();
  const unresolved = new Set<string>();
  for (const raw of tokens) {
    const t = (raw ?? "").trim();
    if (!t) continue;
    if (looksLikeEmail(t)) resolved.add(t.toLowerCase());
    else unresolved.add(t);
  }
  return {
    resolved: Array.from(resolved).sort(),
    unresolved: Array.from(unresolved).sort(),
  };
}

/** Parse a comma/semicolon/whitespace-separated CC string into a deduped list. */
export function parseCcList(raw: string): string[] {
  if (!raw) return [];
  const out = new Set<string>();
  for (const piece of raw.split(/[,;\s]+/)) {
    const v = piece.trim();
    if (v) out.add(v.toLowerCase());
  }
  return Array.from(out);
}

export function isoWeek(d: Date = new Date()): number {
  // ISO 8601 week number — algorithm per https://en.wikipedia.org/wiki/ISO_week_date
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dayNum = t.getUTCDay() || 7; // Mon=1..Sun=7
  t.setUTCDate(t.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
}

export function defaultSubject(opts: {
  project?: string;
  openCount: number;
  blockedCount: number;
  week?: number;
}): string {
  const project = opts.project?.trim() || "All projects";
  const week = opts.week ?? isoWeek();
  const ww = String(week).padStart(2, "0");
  return `[VegaNotes] ${project} kanban — ${opts.openCount} open, ${opts.blockedCount} blocked — ww${ww}`;
}

function formatTaskLine(t: Task): string {
  const parts: string[] = [];
  const prio = PRIORITY_LABELS[t.priority_rank];
  if (prio) parts.push(`[${prio}]`);
  parts.push(t.title || "(no title)");
  const tail: string[] = [];
  if (t.eta) tail.push(`eta ${t.eta}`);
  if (t.owners?.length) tail.push(t.owners.map((o) => `@${o}`).join(" "));
  if (t.task_uuid) tail.push(`(${t.task_uuid})`);
  return `- ${parts.join(" ")}${tail.length ? " — " + tail.join(" — ") : ""}`;
}

export interface BodyOptions {
  filters: FilterState;
  grouped: Record<string, Task[]>;
  columns: readonly string[];
  snapshotUrl: string;
  includeDone: boolean;
  generatedAt?: Date;
}

export function buildPlainBody(opts: BodyOptions): string {
  const { filters, grouped, columns, snapshotUrl, includeDone } = opts;
  const now = opts.generatedAt ?? new Date();
  const lines: string[] = [];
  lines.push("VegaNotes — Kanban snapshot");
  lines.push(`Generated: ${now.toISOString().replace("T", " ").slice(0, 16)} UTC`);

  const filterBits: string[] = [];
  if (filters.project)  filterBits.push(`project=${filters.project}`);
  if (filters.owner)    filterBits.push(`owner=${filters.owner}`);
  if (filters.feature)  filterBits.push(`feature=${filters.feature}`);
  if (filters.priority) filterBits.push(`priority=${filters.priority}`);
  if (filters.status)   filterBits.push(`status=${filters.status}`);
  if (filters.q)        filterBits.push(`q="${filters.q}"`);
  if (filters.where?.length) filterBits.push(`chips=[${filters.where.join(", ")}]`);
  lines.push(`Filters: ${filterBits.length ? filterBits.join(", ") : "(none)"}`);
  lines.push(`View: ${snapshotUrl}`);
  lines.push("");

  for (const col of columns) {
    if (col === "done" && !includeDone) continue;
    const items = grouped[col] ?? [];
    if (!items.length) continue;
    lines.push(`== ${STATUS_LABELS[col] ?? col.toUpperCase()} (${items.length}) ==`);
    for (const t of items) lines.push(formatTaskLine(t));
    lines.push("");
  }
  lines.push("--");
  lines.push("Sent from VegaNotes Kanban view.");
  return lines.join("\n");
}

/** Build a `mailto:` URL. Returns the URL and a flag if it exceeds the safe length. */
export function buildMailto(opts: {
  to: string[];
  cc: string[];
  subject: string;
  body: string;
}): { url: string; tooLong: boolean; length: number } {
  const enc = (s: string) => encodeURIComponent(s);
  const params: string[] = [];
  if (opts.cc.length) params.push(`cc=${opts.cc.map(enc).join(",")}`);
  params.push(`subject=${enc(opts.subject)}`);
  params.push(`body=${enc(opts.body)}`);
  const to = opts.to.map(enc).join(",");
  const url = `mailto:${to}?${params.join("&")}`;
  return { url, tooLong: url.length > 1800, length: url.length };
}

export function countOpen(grouped: Record<string, Task[]>): number {
  return ["todo", "in-progress", "blocked"].reduce((n, c) => n + (grouped[c]?.length ?? 0), 0);
}

/** Truncate body to keep mailto under the safe URL length, preserving header lines. */
export function truncateBodyForMailto(body: string, footer = "\n…(truncated — see snapshot link above)"): string {
  // Caller decides if truncation is needed; this is a safe utility.
  const MAX = 1400; // headroom for to/cc/subject + URL encoding overhead
  if (body.length <= MAX) return body;
  return body.slice(0, MAX - footer.length) + footer;
}
