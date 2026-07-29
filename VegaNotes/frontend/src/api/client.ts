// Tiny typed API client. Auth is HTTP Basic; in single-pod mode the browser
// re-uses the credentials it already negotiated with the page itself.

import { triggerCelebration } from "../lib/celebration";

export interface ChildTask {
  id: number;
  task_uuid: string | null;
  slug: string;
  title: string;
  status: string;
  kind: string;
  line: number;
  eta: string | null;
}

export interface Task {
  id: number;
  task_uuid: string | null;
  slug: string;
  title: string;
  status: string;
  kind: string;
  owners: string[];
  projects: string[];
  features: string[];
  attrs: Record<string, string | string[]>;
  eta: string | null;
  priority_rank: number;
  parent_task_id: number | null;
  note_id: number;
  notes?: string;
  note_history?: string[];
  children?: ChildTask[];
}

export interface TasksResponse {
  tasks: Task[];
  aggregations: {
    owners: string[];
    owner_displays: Record<string, string>;
    projects: string[];
    features: string[];
    status_breakdown: Record<string, number>;
    priority_breakdown: Record<string, number>;
  };
}

export interface AgendaResponse {
  window: { start: string; end: string; days: number };
  by_day: Record<string, Task[]>;
}

const BASE = "/api";

/** Error thrown by `req()` for any non-2xx response. Carries the parsed
 * server `detail` (when JSON) so callers can surface a precise message. */
export class ApiError extends Error {
  status: number;
  detail: string;
  path: string;
  body: unknown;
  constructor(status: number, statusText: string, path: string, detail: string, body: unknown) {
    super(`${status} ${detail || statusText} on ${path}`);
    this.status = status;
    this.path = path;
    this.detail = detail || statusText;
    this.body = body;
  }
}

/** Listeners registered via {@link onAwardedBadges}. Fired once per write
 * response that carries a non-empty `awarded_badges` array. */
type BadgeListener = (badges: string[]) => void;
const _badgeListeners = new Set<BadgeListener>();

/** Subscribe to "badge unlocked" events emitted by any write request.
 *
 * Returns an unsubscribe function. Used by the `<UnlockToast>` component
 * to surface server-awarded badges immediately after the action that
 * earned them, with no per-call-site wiring.
 */
export function onAwardedBadges(fn: BadgeListener): () => void {
  _badgeListeners.add(fn);
  return () => _badgeListeners.delete(fn);
}

function _maybeFireBadges(method: string, body: unknown) {
  if (method === "GET") return;
  if (!body || typeof body !== "object") return;
  const arr = (body as { awarded_badges?: unknown }).awarded_badges;
  if (!Array.isArray(arr) || arr.length === 0) return;
  const keys = arr.filter((k): k is string => typeof k === "string");
  if (keys.length === 0) return;
  for (const fn of _badgeListeners) {
    try { fn(keys); } catch { /* listener errors must not break the request */ }
  }
}

function _maybeFireCelebration(
  path: string,
  method: string,
  reqBody: unknown,
  respBody: unknown,
) {
  // Only PATCH /tasks/<ref> is a candidate (Kanban drag, QuickChips,
  // TaskEditPopover, MyTasksView, cycleAr all funnel through here).
  if (method !== "PATCH") return;
  if (!/^\/tasks\/[^/]+$/.test(path)) return;
  if (!reqBody || typeof reqBody !== "object") return;
  if ((reqBody as { status?: unknown }).status !== "done") return;
  if (!respBody || typeof respBody !== "object") return;
  const resp = respBody as Partial<Task>;
  // Skip AR closures — only top-level task closures celebrate.
  if (resp.kind && resp.kind !== "task") return;
  const prio = (resp.attrs as Record<string, unknown> | undefined)?.priority;
  const prioStr = typeof prio === "string" ? prio.trim().toUpperCase() : "";
  if (prioStr !== "P0") return;
  triggerCelebration({
    priority: "P0",
    sourceId: resp.task_uuid ?? undefined,
    // No origin — overlay defaults to viewport center. Per-card replay
    // buttons still pass an origin when called directly.
  });
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const r = await fetch(BASE + path, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let body: unknown = null;
    let detail = "";
    try {
      body = await r.json();
      if (body && typeof body === "object" && "detail" in (body as any)) {
        const d = (body as any).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      try { detail = await r.text(); } catch { /* ignore */ }
    }
    throw new ApiError(r.status, r.statusText, path, detail, body);
  }
  const parsed = (await r.json()) as T;
  _maybeFireBadges(method, parsed);
  let parsedReq: unknown = undefined;
  if (typeof init?.body === "string") {
    try { parsedReq = JSON.parse(init.body); } catch { /* non-JSON body */ }
  }
  _maybeFireCelebration(path, method, parsedReq, parsed);
  return parsed;
}

export interface ProjectInfo {
  name: string;
  role: "manager" | "member";
}

export interface TreeNote {
  path: string;
  id: number | null;
  title: string;
}

export interface TreeNode {
  project: string | null;
  role: "manager" | "member";
  notes: TreeNote[];
}

export interface ProjectMember {
  user_name: string;
  role: "manager" | "member";
}

export interface ArchivedTask {
  id: number;
  task_uuid: string | null;
  note_path: string;
  title: string;
  status: string;
  priority: string | null;
  eta: string | null;
  kind: string;
  slug: string;
  created_at: string;
  updated_at: string;
  owners: string[];
  projects: string[];
  features: string[];
}

export interface ArchiveSummary {
  total_tasks: number;
  by_status: Record<string, number>;
  by_project: Record<string, number>;
  top_owners: { name: string; count: number }[];
}

// #320: one row from `GET /api/tasks/{ref}/progress-history`.
export interface ProgressHistoryRow {
  /** ISO week, e.g. `"2026-W29"`. */
  week: string;
  numerator: number;
  denominator: number | null;
  label: string | null;
}

export const api = {
  notes: () => req<{ id: number; path: string; title: string }[]>("/notes"),
  note:  (id: number) => req<{ id: number; path: string; title: string; body_md: string; etag: string; prose_etag?: string; tasks_etag?: string }>(`/notes/${id}`),
  noteEtag: (path: string) =>
    req<{ path: string; etag: string; prose_etag?: string; tasks_etag?: string; mtime: number }>(
      `/notes/etag?path=${encodeURIComponent(path)}`,
    ),
  saveNote: (
    path: string,
    body_md: string,
    ifMatch?: string,
    opts?: { ifMatchProse?: string },
  ) =>
    req<{
      id: number;
      path: string;
      etag: string;
      prose_etag?: string;
      tasks_etag?: string;
    }>("/notes", {
      method: "PUT",
      // Design 8d: when ifMatchProse is supplied the backend gates the
      // write on the prose-axis etag and merges live disk task lines on
      // top of the incoming prose. This stops popover ``PATCH /tasks/...``
      // writes from triggering false-positive 409s while the user is
      // typing. Plain `ifMatch` (byte-level) still works for legacy /
      // non-editor callers.
      body: JSON.stringify({
        path,
        body_md,
        ...(opts?.ifMatchProse !== undefined ? { if_match_prose: opts.ifMatchProse } : {}),
      }),
      headers: ifMatch !== undefined ? { "If-Match": ifMatch } : {},
    }),
  deleteNote: (id: number) => req(`/notes/${id}`, { method: "DELETE" }),
  rollNoteNextWeek: (path: string, overwrite = false) =>
    req<{ id: number; path: string; from_ww: number; to_ww: number }>("/notes/next-week", {
      method: "POST", body: JSON.stringify({ path, overwrite }),
    }),
  stampTaskIds: (path: string) =>
    req<{ path: string; injected: number; body_md: string }>("/notes/stamp-ids", {
      method: "POST", body: JSON.stringify({ path }),
    }),
  parsePreview: (body_md: string) => req("/parse", { method: "POST", body: JSON.stringify({ body_md }) }),

  tasks: (params: Record<string, string | string[] | boolean | undefined>) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === "") continue;
      if (Array.isArray(v)) {
        for (const item of v) if (item !== undefined && item !== "") qs.append(k, String(item));
      } else {
        qs.set(k, String(v));
      }
    }
    return req<TasksResponse>(`/tasks?${qs.toString()}`);
  },
  updateTask: (ref: number | string, patch: {
    status?: string;
    priority?: string;
    eta?: string;
    owners?: string[];
    features?: string[];
    // #314: external-URL capsule tokens. Each is a full replacement;
    // pass ``[]`` to clear all values for a key.
    url?: string[];
    hsd?: string[];
    jira?: string[];
    pr?: string[];
    // #320: recurring metric.  Empty string clears the token.
    progress?: string;
    add_note?: string;
    notes?: string;
    title?: string;
  }) =>
    req<Task>(`/tasks/${ref}`, { method: "PATCH", body: JSON.stringify(patch) }),

  createTask: (body: {
    title: string;
    status?: string;
    project?: string;
    note_path?: string;
    owners?: string[];
    priority?: string;
    eta?: string;
    features?: string[];
    kind?: "task" | "ar";
  }) => req<Task & { note_path: string }>("/tasks", { method: "POST", body: JSON.stringify(body) }),

  deleteTask: (ref: number | string) =>
    req<{ status: string; task_uuid: string | null; title: string }>(
      `/tasks/${ref}`,
      { method: "DELETE" },
    ),

  // #320: weekly history for the recurring `#progress` metric.  Rolls up
  // main.db + archive.db and groups by ISO week.
  taskProgressHistory: (ref: number | string) =>
    req<ProgressHistoryRow[]>(`/tasks/${ref}/progress-history`),

  getTask: (ref: number | string, opts?: { includeChildren?: boolean }) => {
    const qs = new URLSearchParams();
    if (opts?.includeChildren) qs.set("include_children", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return req<Task>(`/tasks/${ref}${suffix}`);
  },

  addAr: (ref: number | string, body: { title: string; owners?: string[]; priority?: string; eta?: string; features?: string[] }) =>
    req<Task & { parent_task_uuid: string | null }>(
      `/tasks/${ref}/ars`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  agenda: (owner?: string, days?: number, start?: string, end?: string) => {
    const qs = new URLSearchParams();
    if (days != null) qs.set("days", String(days));
    if (owner) qs.set("owner", owner);
    if (start) qs.set("start", start);
    if (end) qs.set("end", end);
    return req<AgendaResponse>(`/agenda?${qs.toString()}`);
  },
  features: () => req<string[]>("/features"),
  featureTasks: (name: string) =>
    req<{ feature: string; tasks: Task[]; aggregations: any }>(`/features/${encodeURIComponent(name)}/tasks`),
  cardLinks: (ref: number | string) =>
    req<{ task_id: number; task_uuid: string | null; slug: string; links: { other_slug: string; kind: string; direction: "in" | "out" }[] }>(`/cards/${ref}/links`),

  projects: () => req<ProjectInfo[]>("/projects"),
  createProject: (name: string) =>
    req<ProjectInfo>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
  deleteProject: (name: string) =>
    req(`/projects/${encodeURIComponent(name)}`, { method: "DELETE" }),
  projectNotes: (project: string) =>
    req<TreeNote[]>(`/projects/${encodeURIComponent(project)}/notes`),
  tree: () => req<TreeNode[]>("/tree"),

  // ── #304 archive/unarchive ────────────────────────────────────────────
  archiveNote: (id: number) =>
    req<{ archived: number; project?: string; archive_kind?: string }>(
      `/notes/${id}/archive`, { method: "POST" },
    ),
  unarchiveNote: (id: number) =>
    req<{ unarchived: number; notes: string[]; reindexed_tasks: number }>(
      `/notes/${id}/unarchive`, { method: "POST" },
    ),
  archiveProject: (project: string) =>
    req<{ project: string; archived: number; skipped_rollover: number }>(
      `/projects/${encodeURIComponent(project)}/archive`, { method: "POST" },
    ),
  unarchiveProject: (project: string) =>
    req<{ project: string; unarchived: number; reindexed_tasks: number }>(
      `/projects/${encodeURIComponent(project)}/unarchive`, { method: "POST" },
    ),
  archiveReconcile: () =>
    req<{ scanned: number; orphans_dropped: number }>(
      "/archive/reconcile", { method: "POST" },
    ),
  archivedNotes: () =>
    req<{ id: number; path: string; title: string; project: string | null;
          updated_at: string; task_count: number }[]>("/archive/notes"),
  archivedNoteDetail: (id: number) =>
    req<{ id: number; path: string; title: string; body_md: string;
          project: string | null; updated_at: string;
          tasks: ArchivedTask[] }>(`/archive/notes/${id}`),
  archivedTasks: (params?: {
    project?: string; owner?: string; status?: string; q?: string;
    limit?: number; offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.project) qs.set("project", params.project);
    if (params?.owner) qs.set("owner", params.owner);
    if (params?.status) qs.set("status", params.status);
    if (params?.q) qs.set("q", params.q);
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.offset != null) qs.set("offset", String(params.offset));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return req<{ tasks: ArchivedTask[]; total: number }>(
      `/archive/tasks${suffix}`,
    );
  },
  archivedTaskDetail: (task_uuid: string) =>
    req<ArchivedTask>(`/archive/tasks/${task_uuid}`),
  archivedProjects: () =>
    req<{ name: string; archived: true; note_count: number;
          task_count: number }[]>("/archive/projects"),
  archiveSummary: (project?: string) => {
    const suffix = project ? `?project=${encodeURIComponent(project)}` : "";
    return req<ArchiveSummary>(`/archive/summary${suffix}`);
  },

  projectMembers: (project: string) =>
    req<ProjectMember[]>(`/projects/${encodeURIComponent(project)}/members`),
  putProjectMember: (project: string, user_name: string, role: "manager" | "member") =>
    req<ProjectMember>(`/projects/${encodeURIComponent(project)}/members`, {
      method: "PUT",
      body: JSON.stringify({ user_name, role }),
    }),
  removeProjectMember: (project: string, user_name: string) =>
    req(`/projects/${encodeURIComponent(project)}/members/${encodeURIComponent(user_name)}`, {
      method: "DELETE",
    }),

  users: (project?: string) =>
    req<string[]>(
      "/users" + (project ? `?project=${encodeURIComponent(project)}` : ""),
    ),
  usersWithDisplay: (project?: string) =>
    req<{ name: string; display: string }[]>(
      "/users?with_display=1" +
        (project ? `&project=${encodeURIComponent(project)}` : ""),
    ),
  me: () => req<{ name: string; is_admin: boolean; tz?: string }>("/me"),
  changeMyPassword: (current_password: string, new_password: string) =>
    req<{ status: string }>("/me/password", {
      method: "PATCH",
      body: JSON.stringify({ current_password, new_password }),
    }),

  adminListUsers: () =>
    req<{ name: string; is_admin: boolean; has_password: boolean }[]>("/admin/users"),
  adminCreateUser: (name: string, password: string, is_admin = false) =>
    req<{ name: string; is_admin: boolean; has_password: boolean }>("/admin/users", {
      method: "POST",
      body: JSON.stringify({ name, password, is_admin }),
    }),
  adminPatchUser: (
    name: string,
    patch: { password?: string; is_admin?: boolean },
  ) =>
    req<{ name: string; is_admin: boolean; has_password: boolean }>(
      `/admin/users/${encodeURIComponent(name)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    ),
  adminDeleteUser: (name: string) =>
    req(`/admin/users/${encodeURIComponent(name)}`, { method: "DELETE" }),

  adminReindex: () =>
    req<{ status: string; files_indexed: number }>("/admin/reindex", { method: "POST" }),

  search: (q: string) => req<{ id: number; path: string; title: string }[]>(`/search?q=${encodeURIComponent(q)}`),

  // Generic attribute autocomplete (issue #38 follow-up).
  attrs: () => req<{ key: string; count: number; sample_values: string[] }[]>("/attrs"),

  // Per-user saved chip-bar views (issue #38 follow-up).
  //
  // Backend persists views as ``list[{name, query: dict}]`` so they're
  // also addressable from the CLI; the FilterBar deals in opaque DSL
  // "chips" (``string[]``). Bridge here by stashing the chip list as
  // ``query.chips`` and exposing the FE-friendly ``Record<name, chips>``
  // shape that the React component expects. See #236.
  savedViews: async (): Promise<Record<string, string[]>> => {
    const list = await req<Array<{ name: string; query?: { chips?: string[] } }>>(
      "/me/views",
    );
    const out: Record<string, string[]> = {};
    for (const v of list || []) {
      if (!v || typeof v.name !== "string") continue;
      const chips = Array.isArray(v.query?.chips) ? (v.query!.chips as string[]) : [];
      out[v.name] = chips;
    }
    return out;
  },
  saveViews: async (views: Record<string, string[]>): Promise<Record<string, string[]>> => {
    const body = Object.entries(views || {})
      .filter(([name]) => !!name)
      .map(([name, chips]) => ({ name, query: { chips: chips || [] } }));
    await req<{ status: string; count: number }>("/me/views", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    return views;
  },

  // ----- gamification (issue #137) ----------------------------------------
  meStats: () => req<MeStats>("/me/stats"),
  meStreak: () => req<MeStreak>("/me/streak"),
  meHistory: (days: number) => req<MeHistoryDay[]>(`/me/history?days=${days}`),
  meBadges: () => req<MeBadges>("/me/badges"),
  meActivity: (params: { kind?: string; since?: string; until?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.kind) qs.set("kind", params.kind);
    if (params.since) qs.set("since", params.since);
    if (params.until) qs.set("until", params.until);
    if (params.limit != null) qs.set("limit", String(params.limit));
    const tail = qs.toString();
    return req<MeActivityEvent[]>(`/me/activity${tail ? "?" + tail : ""}`);
  },
  setMyTz: (tz: string) =>
    req<{ status: string; tz: string }>("/me/tz", {
      method: "PATCH",
      body: JSON.stringify({ tz }),
    }),

  // ----- phonebook (#174 / #210 Phase 2) --------------------------------
  phonebookResolve: (tokens: string[]) =>
    req<PhonebookResolveResponse>("/phonebook/resolve", {
      method: "POST",
      body: JSON.stringify({ tokens }),
    }),

  // ----- Focus of the Week (#266) ---------------------------------------
  getFocusWeek: async (): Promise<FocusWeek | null> => {
    try {
      return await req<FocusWeek>("/focus-week");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  setFocusWeek: (markdown: string) =>
    req<FocusWeek>("/focus-week", {
      method: "PUT",
      body: JSON.stringify({ markdown }),
    }),

  // ----- Dashboard (issue #290) -------------------------------------------
  dashboardData: (
    project = "ALL",
    range = "H1",
    year?: number,
    since?: string,
    until?: string,
    force = false,
  ) => {
    const qs = new URLSearchParams({ project, range });
    if (year != null) qs.set("year", String(year));
    if (since) qs.set("since", since);
    if (until) qs.set("until", until);
    if (force) qs.set("force", "true");
    return req<DashboardData>(`/dashboard/data?${qs}`);
  },

  dashboardTurnins: (
    project = "ALL",
    engineer?: string,
    range = "H1",
    year?: number,
    since?: string,
    until?: string,
    force = false,
  ) => {
    const qs = new URLSearchParams({ project, range });
    if (engineer) qs.set("engineer", engineer);
    if (year != null) qs.set("year", String(year));
    if (since) qs.set("since", since);
    if (until) qs.set("until", until);
    if (force) qs.set("force", "true");
    return req<DashboardTurnins>(`/dashboard/turnins?${qs}`);
  },

  dashboardRoster: () => req<string[]>("/dashboard/roster"),
};

export interface FocusWeek {
  markdown: string;
  updated_at: string | null;
  path: string;
}

export interface PhonebookEntry {
  idsid: string;
  display: string;
  email: string;
  aliases: string[];
  manager_email: string | null;
}

export interface PhonebookResolveResponse {
  resolved: Record<string, PhonebookEntry>;
  ambiguous: Record<string, PhonebookEntry[]>;
  unresolved: string[];
}

// ----- gamification response shapes (issue #137) --------------------------

export interface MeStats {
  as_of: string;
  tz: string;
  tasks_closed: { today: number; week: number; month: number; lifetime: number };
  notes_touched: { week: number; month: number };
  current_streak_days: number;
  longest_streak_days: number;
  rest_tokens_remaining: number;
  on_time_eta_rate_30d: number | null;
  on_time_sample_30d: number;
  favorite_project_30d: string | null;
  by_kind: Record<string, number>;
}

export interface MeStreak {
  current_streak_days: number;
  longest_streak_days: number;
  rest_tokens_remaining: number;
  as_of: string;
}

export interface MeHistoryDay {
  date: string;
  closes: number;
  edits: number;
}

export interface EarnedBadge {
  key: string;
  title: string;
  description: string;
  awarded_at: string;
}

export interface LockedBadge {
  key: string;
  title: string;
  description: string;
  progress: number | null;
}

export interface MeBadges {
  earned: EarnedBadge[];
  locked: LockedBadge[];
  hidden_locked_count: number;
  total_count: number;
}

export interface MeActivityEvent {
  id: number;
  kind: string;
  ref: string | null;
  ts: string;
  meta: Record<string, unknown>;
}

// ----- Dashboard (issue #290) shapes ----------------------------------------

export interface DashboardWindow {
  since: string;
  until: string;
  range: string;
  year: number;
  label: string;
}

export interface DashboardFileStat {
  path: string;
  add: number;
  del: number;
}

export interface DashboardCommit {
  sha: string;
  full_sha: string;
  date: string;
  subject: string;
  add: number;
  del: number;
  net: number;
  files: number;
  file_stats: DashboardFileStat[];
  project?: string;
}

export interface DashboardFileStats {
  commits: number;
  add: number;
  del: number;
  net: number;
  commits_list: {
    sha: string;
    subject: string;
    date: string;
    add: number;
    del: number;
    net: number;
    project?: string;
  }[];
  project?: string;
  path?: string;
}

export interface EngineerData {
  engineer: string;
  idsid: string;
  wwid: string;
  total: number;
  net_lines: number;
  avg_lines: number;
  median_lines: number;
  at_or_below: number;
  pct_at_or_below: number;
  pattern: string;
  monthly: Record<string, { commits: number; net: number }>;
  categories: Record<string, number>;
  files: Record<string, DashboardFileStats>;
  commits: DashboardCommit[];
  per_project: Record<string, EngineerData>;
}

export interface DashboardData {
  generated_at: string;
  window: DashboardWindow;
  project: string;
  repos: Record<string, string>;
  engineers: EngineerData[];
  identities: Record<string, { idsid: string; wwid: string }>;
  team_totals: { total: number; net_lines: number; engineers: number };
  team_totals_by_project: Record<string, { total: number; net_lines: number }>;
  team_categories: Record<string, number>;
  team_monthly: Record<string, number>;
  team_monthly_by_project: Record<string, Record<string, number>>;
  categories: string[];
  months: string[];
}

export interface TurninCommit {
  sha: string;
  merge: boolean;
  subject: string;
  author: string;
  date: string;
}

export interface TurninRecord {
  id: number | string;
  bundle_id?: string | null;
  status: string;
  stage?: string | null;
  cluster?: string | null;
  stepping?: string | null;
  branch?: string | null;
  model?: string | null;
  turnin_time: string;
  completed_time?: string | null;
  comments: string;
  files_changed: string[];
  code_review_url?: string | null;
  code_review_status?: string | null;
  user_commit?: string | null;
  bundle_commit?: string | null;
  bugs: string[];
  ecos: string[];
  commits: TurninCommit[];
  n_commits: number;
  hsds_added: string[];
  project: string;
}

/** Single-engineer turnin report (from build_turnin_report). */
export interface TurninReport {
  engineer: string;
  idsid: string;
  project: string;
  window: DashboardWindow;
  totals: Record<string, number>;
  turnins: TurninRecord[];
}

export interface EngineerTurninsEntry {
  engineer: string;
  idsid: string;
  total: number;
  released: number;
  files: number;
  released_files: number;
  per_project: Record<string, { total: number; released: number; files: number; released_files: number }>;
  monthly: Record<string, number>;
  monthly_released: Record<string, number>;
  status: Record<string, number>;
}

/** Full-team turnin summary (from build_team_turnin_summary). */
export interface TeamTurninsReport {
  project: string;
  window: DashboardWindow;
  months: string[];
  team_totals: { turnins: number; files: number; released: number; engineers: number };
  team_totals_by_project: Record<string, number>;
  team_totals_released_by_project: Record<string, number>;
  team_monthly: Record<string, number>;
  team_monthly_released: Record<string, number>;
  team_monthly_by_project: Record<string, Record<string, number>>;
  team_monthly_released_by_project: Record<string, Record<string, number>>;
  status_counts: Record<string, number>;
  engineers: EngineerTurninsEntry[];
  generated_at: string;
}

/** Union — discriminate via `"turnins" in data` (TurninReport) vs `"engineers" in data` (TeamTurninsReport). */
export type DashboardTurnins = TurninReport | TeamTurninsReport;
