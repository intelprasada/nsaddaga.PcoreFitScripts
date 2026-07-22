import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";

/**
 * Archive view (#304 PR 5).
 *
 * Top pane: aggregate stats sourced from ``/api/archive/summary``.
 * Middle pane: list of archived notes with per-row unarchive buttons.
 * Bottom pane: list of archived projects with per-row unarchive buttons.
 *
 * All mutations invalidate the tree/tasks/notes queries so the sidebar
 * re-hydrates without a manual refresh.
 */
export function ArchiveView() {
  const qc = useQueryClient();
  const [scopeProject, setScopeProject] = useState<string>("");

  const { data: summary } = useQuery({
    queryKey: ["archive-summary", scopeProject],
    queryFn: () => api.archiveSummary(scopeProject || undefined),
  });
  const { data: notes = [] } = useQuery({
    queryKey: ["archive-notes"],
    queryFn: () => api.archivedNotes(),
  });
  const { data: projects = [] } = useQuery({
    queryKey: ["archive-projects"],
    queryFn: () => api.archivedProjects(),
  });

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ["archive-summary"] });
    qc.invalidateQueries({ queryKey: ["archive-notes"] });
    qc.invalidateQueries({ queryKey: ["archive-projects"] });
    qc.invalidateQueries({ queryKey: ["tree"] });
    qc.invalidateQueries({ queryKey: ["notes"] });
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["projects"] });
  };

  const unarchiveNote = useMutation({
    mutationFn: (id: number) => api.unarchiveNote(id),
    onSuccess: refreshAll,
    onError: (e: any) => alert(`Unarchive failed: ${e?.message ?? e}`),
  });
  const unarchiveProject = useMutation({
    mutationFn: (project: string) => api.unarchiveProject(project),
    onSuccess: refreshAll,
    onError: (e: any) => alert(`Unarchive failed: ${e?.message ?? e}`),
  });

  const scopeOptions = useMemo(() => {
    const names = new Set<string>();
    for (const p of projects) names.add(p.name);
    for (const n of notes) if (n.project) names.add(n.project);
    return [...names].sort();
  }, [projects, notes]);

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <header className="flex items-center gap-3 border-b pb-3">
        <h1 className="text-xl font-semibold">🗄️ Archive</h1>
        <span className="text-xs text-slate-500">
          frozen state of user-archived projects &amp; notes
        </span>
        <div className="ml-auto">
          <label className="text-xs text-slate-500 mr-1">Scope:</label>
          <select
            className="text-sm border rounded px-2 py-1"
            value={scopeProject}
            onChange={(e) => setScopeProject(e.target.value)}
          >
            <option value="">All projects</option>
            {scopeOptions.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
      </header>

      <section className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <SummaryCard title="Total tasks" value={summary?.total_tasks ?? 0} />
        <SummaryCard
          title="Done"
          value={summary?.by_status?.done ?? 0}
          hint={
            summary
              ? Object.entries(summary.by_status)
                  .filter(([k]) => k !== "done")
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(" · ")
              : ""
          }
        />
        <SummaryCard
          title="Projects"
          value={Object.keys(summary?.by_project ?? {}).length}
        />
        <SummaryCard
          title="Top owner"
          value={summary?.top_owners?.[0]?.name ?? "—"}
          hint={
            summary?.top_owners?.[0]
              ? `${summary.top_owners[0].count} tasks`
              : ""
          }
        />
      </section>

      {summary && summary.top_owners.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-slate-700 mb-2">
            Top owners
          </h2>
          <div className="flex flex-wrap gap-2">
            {summary.top_owners.map((o) => (
              <span
                key={o.name}
                className="text-xs bg-slate-100 border rounded-full px-2 py-1"
              >
                @{o.name} · <b>{o.count}</b>
              </span>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="text-sm font-medium text-slate-700 mb-2">
          Archived projects{" "}
          <span className="text-xs text-slate-400">({projects.length})</span>
        </h2>
        {projects.length === 0 ? (
          <div className="text-sm text-slate-500 italic">
            No archived projects.
          </div>
        ) : (
          <ul className="divide-y border rounded">
            {projects.map((p) => (
              <li
                key={p.name}
                className="flex items-center gap-3 px-3 py-2 text-sm"
              >
                <span className="font-medium">{p.name}</span>
                <span className="text-xs text-slate-500">
                  {p.note_count} notes · {p.task_count} tasks
                </span>
                <button
                  className="ml-auto text-xs px-2 py-1 border rounded hover:bg-slate-50"
                  onClick={() => {
                    if (window.confirm(`Unarchive project "${p.name}"?`))
                      unarchiveProject.mutate(p.name);
                  }}
                >
                  ↩ unarchive
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="text-sm font-medium text-slate-700 mb-2">
          Archived notes{" "}
          <span className="text-xs text-slate-400">({notes.length})</span>
        </h2>
        {notes.length === 0 ? (
          <div className="text-sm text-slate-500 italic">
            No archived notes.
          </div>
        ) : (
          <ul className="divide-y border rounded">
            {notes.map((n) => (
              <li
                key={n.id}
                className="flex items-center gap-3 px-3 py-2 text-sm"
              >
                <span className="font-medium">{n.title || n.path}</span>
                <span className="text-xs text-slate-500 truncate">
                  {n.path}
                </span>
                <span className="text-xs text-slate-500 whitespace-nowrap">
                  {n.task_count} tasks
                </span>
                <button
                  className="ml-auto text-xs px-2 py-1 border rounded hover:bg-slate-50"
                  onClick={() => {
                    if (window.confirm(`Unarchive note "${n.path}"?`))
                      unarchiveNote.mutate(n.id);
                  }}
                >
                  ↩ unarchive
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function SummaryCard({
  title,
  value,
  hint,
}: {
  title: string;
  value: number | string;
  hint?: string;
}) {
  return (
    <div className="border rounded p-3 bg-white">
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {title}
      </div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
      {hint && (
        <div className="text-xs text-slate-400 mt-1 truncate" title={hint}>
          {hint}
        </div>
      )}
    </div>
  );
}
