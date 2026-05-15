import type { Task, PhonebookEntry } from "../../api/client";
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

/** Partition a list of owner tokens into email-shaped vs not-yet-resolvable.
 *
 * If a `phonebook` map is provided, tokens are first looked up there; matched
 * entries contribute their canonical `email` (and the original token is also
 * remembered in `displayByEmail` so the body can show "Prasad <email>"). */
export function partitionOwners(
  tokens: Iterable<string>,
  phonebook?: Record<string, PhonebookEntry>,
): EmailRecipients & { displayByEmail: Record<string, string> } {
  const resolved = new Set<string>();
  const unresolved = new Set<string>();
  const displayByEmail: Record<string, string> = {};
  for (const raw of tokens) {
    const t = (raw ?? "").trim();
    if (!t) continue;
    // 1. Phonebook hit (covers @nsaddaga, @Prasad, "Prasad Addagarla", ...).
    const pbHit = phonebook ? lookupResolved(phonebook, t) : null;
    if (pbHit) {
      const em = pbHit.email.toLowerCase();
      resolved.add(em);
      displayByEmail[em] = pbHit.display || pbHit.idsid;
      continue;
    }
    // 2. Token already looks like an email.
    if (looksLikeEmail(t)) {
      const em = t.toLowerCase();
      resolved.add(em);
      if (!displayByEmail[em]) displayByEmail[em] = t;
      continue;
    }
    unresolved.add(t);
  }
  return {
    resolved: Array.from(resolved).sort(),
    unresolved: Array.from(unresolved).sort(),
    displayByEmail,
  };
}

export function lookupResolved(
  pb: Record<string, PhonebookEntry>,
  token: string,
): PhonebookEntry | null {
  const stripped = token.replace(/^@/, "").trim();
  if (!stripped) return null;
  // Direct key (the API returns entries keyed by the original token).
  if (pb[token]) return pb[token];
  if (pb[stripped]) return pb[stripped];
  // Defensive: case-insensitive fallback.
  const lc = stripped.toLowerCase();
  for (const k of Object.keys(pb)) {
    if (k.replace(/^@/, "").toLowerCase() === lc) return pb[k];
  }
  return null;
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

function formatTaskLine(t: Task, phonebook?: Record<string, PhonebookEntry>): string {
  const parts: string[] = [];
  const prio = PRIORITY_LABELS[t.priority_rank];
  if (prio) parts.push(`[${prio}]`);
  parts.push(t.title || "(no title)");
  const tail: string[] = [];
  if (t.eta) tail.push(`eta ${t.eta}`);
  if (t.owners?.length) {
    const labels = t.owners.map((o) => {
      const pb = phonebook ? lookupResolved(phonebook, o) : null;
      return pb ? `@${pb.idsid}` : `@${o}`;
    });
    tail.push(labels.join(" "));
  }
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
  /** Optional phonebook map for owner display-name resolution in the table. */
  phonebook?: Record<string, PhonebookEntry>;
}

/** Per-owner counts of tasks by column. Sorted by total descending. */
export interface OwnerStatusRow {
  owner: string;        // display name (or token if unresolved)
  email: string | null; // resolved email, if any
  todo: number;
  inProgress: number;
  blocked: number;
  done: number;
  total: number;
}

const UNASSIGNED = "(unassigned)";

export function buildOwnerStatusRows(
  tasks: Task[],
  phonebook?: Record<string, PhonebookEntry>,
): OwnerStatusRow[] {
  // Key by canonical id: email if resolved, else lowercased token, else UNASSIGNED.
  const rows = new Map<string, OwnerStatusRow>();
  const bumpFor = (key: string, display: string, email: string | null, status: string) => {
    let row = rows.get(key);
    if (!row) {
      row = { owner: display, email, todo: 0, inProgress: 0, blocked: 0, done: 0, total: 0 };
      rows.set(key, row);
    }
    row.total += 1;
    if (status === "todo") row.todo += 1;
    else if (status === "in-progress") row.inProgress += 1;
    else if (status === "blocked") row.blocked += 1;
    else if (status === "done") row.done += 1;
  };
  for (const t of tasks) {
    const owners = t.owners?.length ? t.owners : [UNASSIGNED];
    for (const o of owners) {
      const tok = (o ?? "").trim();
      if (!tok || tok === UNASSIGNED) {
        bumpFor("__unassigned__", UNASSIGNED, null, t.status);
        continue;
      }
      const pb = phonebook ? lookupResolved(phonebook, tok) : null;
      if (pb) {
        bumpFor(pb.email.toLowerCase(), pb.display || pb.idsid, pb.email, t.status);
      } else if (looksLikeEmail(tok)) {
        bumpFor(tok.toLowerCase(), tok, tok.toLowerCase(), t.status);
      } else {
        bumpFor(tok.toLowerCase(), `@${tok}`, null, t.status);
      }
    }
  }
  return Array.from(rows.values()).sort((a, b) => b.total - a.total || a.owner.localeCompare(b.owner));
}

/** Render owner-status rows as a fixed-width ASCII table (Outlook-friendly
 * when the user pastes plain text). Inspired by the `vn list` output. */
export function renderOwnerStatusTable(rows: OwnerStatusRow[]): string {
  if (rows.length === 0) return "(no tasks)";
  const headers = ["Owner", "Todo", "WIP", "Blkd", "Done", "Total"];
  const cells: string[][] = rows.map((r) => [
    truncate(r.owner, 32),
    String(r.todo),
    String(r.inProgress),
    String(r.blocked),
    String(r.done),
    String(r.total),
  ]);
  const widths = headers.map((h, i) =>
    Math.max(h.length, ...cells.map((c) => c[i].length)),
  );
  const align = ["left", "right", "right", "right", "right", "right"] as const;
  const fmtRow = (cols: string[]) =>
    cols.map((c, i) => align[i] === "right" ? c.padStart(widths[i]) : c.padEnd(widths[i])).join("  ");
  const sep = widths.map((w) => "-".repeat(w)).join("  ");
  const out: string[] = [];
  out.push(fmtRow(headers));
  out.push(sep);
  for (const c of cells) out.push(fmtRow(c));
  // Totals row.
  const totals = ["Total",
    sum(rows, (r) => r.todo),
    sum(rows, (r) => r.inProgress),
    sum(rows, (r) => r.blocked),
    sum(rows, (r) => r.done),
    sum(rows, (r) => r.total),
  ].map(String);
  out.push(sep);
  out.push(fmtRow(totals));
  return out.join("\n");
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}
function sum<T>(xs: T[], pick: (x: T) => number): number {
  let acc = 0;
  for (const x of xs) acc += pick(x);
  return acc;
}

export function buildPlainBody(opts: BodyOptions): string {
  const { filters, grouped, columns, snapshotUrl, includeDone, phonebook } = opts;
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

  // Per-owner status summary table (#210 Phase 2). Outlook-friendly fixed-width.
  const visibleTasks: Task[] = [];
  for (const col of columns) {
    if (col === "done" && !includeDone) continue;
    visibleTasks.push(...(grouped[col] ?? []));
  }
  if (visibleTasks.length > 0) {
    lines.push("== STATUS BY OWNER ==");
    lines.push(renderOwnerStatusTable(buildOwnerStatusRows(visibleTasks, phonebook)));
    lines.push("");
  }

  for (const col of columns) {
    if (col === "done" && !includeDone) continue;
    const items = grouped[col] ?? [];
    if (!items.length) continue;
    lines.push(`== ${STATUS_LABELS[col] ?? col.toUpperCase()} (${items.length}) ==`);
    for (const t of items) lines.push(formatTaskLine(t, phonebook));
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

// ---------------------------------------------------------------------------
// HTML body (#219). Outlook-safe: <table> layout, inline styles, web-safe
// colors, no flexbox / CSS variables / background images. Designed to be
// copied to the clipboard as a `text/html` blob via ClipboardItem so the
// user pastes a formatted snapshot into Outlook/Gmail/etc.
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  "blocked":     { bg: "#dc2626", fg: "#ffffff" }, // red-600
  "in-progress": { bg: "#2563eb", fg: "#ffffff" }, // blue-600
  "todo":        { bg: "#475569", fg: "#ffffff" }, // slate-600
  "done":        { bg: "#16a34a", fg: "#ffffff" }, // green-600
};

const PRIORITY_COLORS: Record<number, string> = {
  1: "#dc2626", // P1 red
  2: "#ea580c", // P2 orange
  3: "#ca8a04", // P3 amber
  4: "#65a30d", // P4 lime
  5: "#0891b2", // P5 cyan
};

function escHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function htmlOwnerLabels(t: Task, phonebook?: Record<string, PhonebookEntry>): string {
  if (!t.owners?.length) return "";
  const chips = t.owners.map((o) => {
    const pb = phonebook ? lookupResolved(phonebook, o) : null;
    const label = pb ? (pb.display || pb.idsid) : `@${o}`;
    return `<span style="display:inline-block;padding:1px 6px;margin-right:4px;border-radius:10px;background:#e2e8f0;color:#0f172a;font-size:11px;">${escHtml(label)}</span>`;
  });
  return chips.join("");
}

function htmlOwnerStatusTable(rows: OwnerStatusRow[]): string {
  if (rows.length === 0) return "";
  const head = `
    <tr style="background:#f1f5f9;">
      <th align="left"  style="padding:6px 10px;font-size:12px;color:#334155;border-bottom:1px solid #cbd5e1;">Owner</th>
      <th align="right" style="padding:6px 10px;font-size:12px;color:#334155;border-bottom:1px solid #cbd5e1;">Todo</th>
      <th align="right" style="padding:6px 10px;font-size:12px;color:#334155;border-bottom:1px solid #cbd5e1;">WIP</th>
      <th align="right" style="padding:6px 10px;font-size:12px;color:#334155;border-bottom:1px solid #cbd5e1;">Blkd</th>
      <th align="right" style="padding:6px 10px;font-size:12px;color:#334155;border-bottom:1px solid #cbd5e1;">Done</th>
      <th align="right" style="padding:6px 10px;font-size:12px;color:#334155;border-bottom:1px solid #cbd5e1;">Total</th>
    </tr>`;
  const body = rows.map((r, i) => {
    const bg = i % 2 === 0 ? "#ffffff" : "#f8fafc";
    return `
      <tr style="background:${bg};">
        <td style="padding:5px 10px;font-size:13px;color:#0f172a;">${escHtml(r.owner)}</td>
        <td align="right" style="padding:5px 10px;font-size:13px;color:#475569;">${r.todo}</td>
        <td align="right" style="padding:5px 10px;font-size:13px;color:#2563eb;">${r.inProgress}</td>
        <td align="right" style="padding:5px 10px;font-size:13px;color:#dc2626;">${r.blocked}</td>
        <td align="right" style="padding:5px 10px;font-size:13px;color:#16a34a;">${r.done}</td>
        <td align="right" style="padding:5px 10px;font-size:13px;color:#0f172a;font-weight:600;">${r.total}</td>
      </tr>`;
  }).join("");
  return `
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;border:1px solid #cbd5e1;border-radius:4px;margin:0 0 16px 0;">
      ${head}
      ${body}
    </table>`;
}

function htmlColumnSection(col: string, items: Task[], snapshotUrl: string, phonebook?: Record<string, PhonebookEntry>): string {
  if (!items.length) return "";
  const c = STATUS_COLORS[col] ?? { bg: "#475569", fg: "#ffffff" };
  const label = STATUS_LABELS[col] ?? col.toUpperCase();
  const rows = items.map((t) => {
    const prio = PRIORITY_LABELS[t.priority_rank];
    const prioColor = PRIORITY_COLORS[t.priority_rank] ?? "#64748b";
    const prioChip = prio
      ? `<span style="display:inline-block;padding:1px 6px;border-radius:10px;background:${prioColor};color:#ffffff;font-size:11px;font-weight:600;">${prio}</span>`
      : "";
    const eta = t.eta
      ? `<span style="display:inline-block;padding:1px 6px;border-radius:3px;background:#fef3c7;color:#92400e;font-size:11px;margin-left:6px;">ETA ${escHtml(t.eta)}</span>`
      : "";
    const owners = htmlOwnerLabels(t, phonebook);
    const projects = (t.projects?.length ? t.projects.join(" / ") : "");
    const path = projects ? `<div style="font-size:11px;color:#94a3b8;margin-top:2px;">${escHtml(projects)}</div>` : "";
    const titleLink = t.task_uuid && snapshotUrl
      ? `<a href="${escHtml(snapshotUrl)}#task=${escHtml(t.task_uuid)}" style="color:#0f172a;text-decoration:none;font-weight:500;">${escHtml(t.title || "(no title)")}</a>`
      : `<span style="color:#0f172a;font-weight:500;">${escHtml(t.title || "(no title)")}</span>`;
    return `
      <tr>
        <td style="padding:8px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top;width:60px;">
          ${prioChip}
        </td>
        <td style="padding:8px 10px;border-bottom:1px solid #e2e8f0;">
          ${titleLink}${eta}
          ${owners ? `<div style="margin-top:4px;">${owners}</div>` : ""}
          ${path}
        </td>
      </tr>`;
  }).join("");
  return `
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;border:1px solid #e2e8f0;border-radius:4px;margin:0 0 14px 0;">
      <tr style="background:${c.bg};">
        <td colspan="2" style="padding:6px 12px;color:${c.fg};font-size:13px;font-weight:600;letter-spacing:0.3px;">
          ${label} <span style="opacity:0.85;font-weight:400;">(${items.length})</span>
        </td>
      </tr>
      ${rows}
    </table>`;
}

export function buildHtmlBody(opts: BodyOptions): string {
  const { filters, grouped, columns, snapshotUrl, includeDone, phonebook } = opts;
  const now = opts.generatedAt ?? new Date();
  const project = filters.project?.trim() || "All projects";
  const filterBits: string[] = [];
  if (filters.owner)    filterBits.push(`owner=${filters.owner}`);
  if (filters.feature)  filterBits.push(`feature=${filters.feature}`);
  if (filters.priority) filterBits.push(`priority=${filters.priority}`);
  if (filters.status)   filterBits.push(`status=${filters.status}`);
  if (filters.q)        filterBits.push(`q="${filters.q}"`);
  if (filters.where?.length) filterBits.push(`chips=[${filters.where.join(", ")}]`);
  const filterTxt = filterBits.length ? filterBits.join(", ") : "(no filters)";
  const openCount = countOpen(grouped);
  const blockedCount = grouped["blocked"]?.length ?? 0;

  const visibleTasks: Task[] = [];
  for (const col of columns) {
    if (col === "done" && !includeDone) continue;
    visibleTasks.push(...(grouped[col] ?? []));
  }
  const ownerTable = visibleTasks.length > 0
    ? htmlOwnerStatusTable(buildOwnerStatusRows(visibleTasks, phonebook))
    : "";

  const sections = columns
    .filter((col) => col !== "done" || includeDone)
    .map((col) => htmlColumnSection(col, grouped[col] ?? [], snapshotUrl, phonebook))
    .join("");

  const generated = now.toISOString().replace("T", " ").slice(0, 16);
  const link = snapshotUrl
    ? `<a href="${escHtml(snapshotUrl)}" style="color:#0369a1;">View live in VegaNotes</a>`
    : "";

  return `
<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0f172a;max-width:640px;margin:0 auto;">
  <div style="border-bottom:2px solid #0f172a;padding-bottom:8px;margin-bottom:14px;">
    <div style="font-size:18px;font-weight:600;">${escHtml(project)} &mdash; Kanban snapshot</div>
    <div style="font-size:12px;color:#64748b;margin-top:4px;">
      ${openCount} open &middot; ${blockedCount} blocked &middot;
      Filters: ${escHtml(filterTxt)} &middot;
      Generated ${escHtml(generated)} UTC
    </div>
  </div>
  ${ownerTable ? `<div style="font-size:12px;font-weight:600;color:#334155;margin:0 0 6px 0;letter-spacing:0.4px;">STATUS BY OWNER</div>${ownerTable}` : ""}
  ${sections}
  <div style="margin-top:14px;padding-top:8px;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;">
    ${link} &middot; Sent from VegaNotes Kanban view
  </div>
</div>`.trim();
}
