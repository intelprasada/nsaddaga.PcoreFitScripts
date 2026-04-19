// Tiny typed API client. Auth is HTTP Basic; in single-pod mode the browser
// re-uses the credentials it already negotiated with the page itself.

export interface ChildTask {
  id: number;
  slug: string;
  title: string;
  status: string;
  kind: string;
  line: number;
  eta: string | null;
}

export interface Task {
  id: number;
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

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
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
  parsePreview: (body_md: string) => req("/parse", { method: "POST", body: JSON.stringify({ body_md }) }),

  tasks: (params: Record<string, string | boolean | undefined>) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
    return req<TasksResponse>(`/tasks?${qs.toString()}`);
  },
  updateTask: (id: number, patch: { status?: string }) =>
    req<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  agenda: (owner?: string, days = 7) => {
    const qs = new URLSearchParams({ days: String(days) });
    if (owner) qs.set("owner", owner);
    return req<AgendaResponse>(`/agenda?${qs.toString()}`);
  },
  features: () => req<string[]>("/features"),
  featureTasks: (name: string) =>
    req<{ feature: string; tasks: Task[]; aggregations: any }>(`/features/${encodeURIComponent(name)}/tasks`),
  cardLinks: (id: number) =>
    req<{ task_id: number; slug: string; links: { other_slug: string; kind: string; direction: "in" | "out" }[] }>(`/cards/${id}/links`),

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
  search: (q: string) => req<{ id: number; path: string; title: string }[]>(`/search?q=${encodeURIComponent(q)}`),
};
