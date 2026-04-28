// Tiny typed API client. Auth is HTTP Basic; in single-pod mode the browser
// re-uses the credentials it already negotiated with the page itself.

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

async function req<T>(path: string, init?: RequestInit): Promise<T> {
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
  return r.json() as Promise<T>;
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

export const api = {
  notes: () => req<{ id: number; path: string; title: string }[]>("/notes"),
  note:  (id: number) => req<{ id: number; path: string; title: string; body_md: string }>(`/notes/${id}`),
  saveNote: (path: string, body_md: string) =>
    req<{ id: number; path: string }>("/notes", { method: "PUT", body: JSON.stringify({ path, body_md }) }),
  deleteNote: (id: number) => req(`/notes/${id}`, { method: "DELETE" }),
  rollNoteNextWeek: (path: string, overwrite = false) =>
    req<{ id: number; path: string; from_ww: number; to_ww: number }>("/notes/next-week", {
      method: "POST", body: JSON.stringify({ path, overwrite }),
    }),
  stampTaskIds: (path: string) =>
    req<{ path: string; injected: number; body_md: string }>("/notes/stamp-ids", {
      method: "POST", body: JSON.stringify({ path }),
    }),
  noteAbsPath: (path: string) =>
    req<{ path: string; abs_path: string; vim_cmd: string }>(
      `/notes/abs-path?path=${encodeURIComponent(path)}`,
    ),
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
  updateTask: (id: number, patch: {
    status?: string;
    priority?: string;
    eta?: string;
    owners?: string[];
    features?: string[];
    add_note?: string;
    notes?: string;
  }) =>
    req<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

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

  users: () => req<string[]>("/users"),
  me: () => req<{ name: string; is_admin: boolean }>("/me"),
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
  savedViews: () => req<Record<string, string[]>>("/me/views"),
  saveViews: (views: Record<string, string[]>) =>
    req<Record<string, string[]>>("/me/views", { method: "PUT", body: JSON.stringify(views) }),
};
