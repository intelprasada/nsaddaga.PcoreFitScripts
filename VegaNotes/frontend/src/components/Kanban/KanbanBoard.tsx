import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { TaskEditPopover } from "../Tasks/TaskEditPopover";
import { useUI } from "../../store/ui";

const COLUMNS = ["todo", "in-progress", "blocked", "done"] as const;

/**
 * Kanban board.
 *
 * Status changes happen via ◀/▶ buttons on each card — no drag-and-drop.
 * That tradeoff: arrows are reliable, work on touch, don't repaint the
 * whole board on every pointer move, and skip the per-frame transform
 * math that made the dnd-kit approach feel sluggish (issue #58 follow-up).
 */
export function KanbanBoard() {
  const { filters } = useUI();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Task | null>(null);
  const { data } = useQuery({
    queryKey: ["tasks", filters, "kanban"],
    queryFn: () =>
      api.tasks({
        ...filters,
        hide_done: false,
        top_level_only: true,
        include_children: true,
      }),
  });
  const tasks = data?.tasks ?? [];
  const grouped = useMemo(() => {
    const g: Record<string, Task[]> = {};
    for (const c of COLUMNS) g[c] = [];
    for (const t of tasks) (g[t.status] ?? g.todo).push(t);
    return g;
  }, [tasks]);

  const move = useMutation({
    mutationFn: ({ task, newStatus }: { task: Task; newStatus: string }) =>
      api.updateTask(task.id, { status: newStatus }),
    // Optimistic: flip the card's status in cache immediately so the UI
    // moves on the next frame instead of waiting for the PATCH round-trip
    // (which on a large file can take 1-2 s due to the full reindex; see
    // issues #50 and #51).
    onMutate: async ({ task, newStatus }) => {
      await qc.cancelQueries({ queryKey: ["tasks"] });
      const snapshots: Array<[readonly unknown[], unknown]> = [];
      qc.getQueriesData({ queryKey: ["tasks"] }).forEach(([key, data]) => {
        snapshots.push([key, data]);
        if (!data || typeof data !== "object") return;
        const d = data as { tasks?: Task[] };
        if (!Array.isArray(d.tasks)) return;
        qc.setQueryData(key, {
          ...d,
          tasks: d.tasks.map((t) =>
            t.id === task.id ? { ...t, status: newStatus } : t,
          ),
        });
      });
      return { snapshots };
    },
    onError: (_err, _vars, ctx) => {
      // Roll back on failure.
      ctx?.snapshots.forEach(([key, data]) => qc.setQueryData(key, data));
    },
    onSettled: () => {
      // Reconcile in the background. The optimistic state already shows
      // the right column, so this refetch is invisible to the user.
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["agenda"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
  });

  const setStatus = (task: Task, newStatus: string) => {
    if (newStatus === task.status) return;
    move.mutate({ task, newStatus });
  };

  return (
    <>
      <div className="flex gap-3 p-4 overflow-x-auto">
        {COLUMNS.map((c) => (
          <div key={c} className="flex-1 min-w-[260px] rounded-lg p-3 bg-slate-100">
            <div className="text-xs uppercase tracking-wide text-slate-500 mb-2 flex justify-between">
              <span>{c}</span><span>{grouped[c].length}</span>
            </div>
            <div className="space-y-2">
              {grouped[c].map((t) => (
                <div key={t.id} className="relative group">
                  <TaskCard task={t} onOpen={setEditing} />
                  <select
                    aria-label="Set status"
                    title="Set status"
                    value={t.status}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => { e.stopPropagation(); setStatus(t, e.target.value); }}
                    className="absolute top-1 right-1 z-10 text-[11px] rounded border border-slate-300 bg-white/95 px-1 py-0.5 text-slate-700 opacity-60 group-hover:opacity-100 transition cursor-pointer hover:border-slate-400"
                  >
                    {COLUMNS.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {editing && <TaskEditPopover task={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
