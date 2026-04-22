import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { TaskEditPopover } from "../Tasks/TaskEditPopover";
import { useUI } from "../../store/ui";

const COLUMNS = ["todo", "in-progress", "blocked", "done"] as const;

/**
 * Kanban board — read-only grouping view.
 *
 * Status changes happen by clicking the card to open the edit popover.
 * Inline status controls (drag-and-drop / arrows / dropdown) were all
 * tried and removed for UX reasons — see issues #58 and follow-ups.
 */
export function KanbanBoard() {
  const { filters } = useUI();
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
                <TaskCard key={t.id} task={t} onOpen={setEditing} />
              ))}
            </div>
          </div>
        ))}
      </div>
      {editing && <TaskEditPopover task={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
