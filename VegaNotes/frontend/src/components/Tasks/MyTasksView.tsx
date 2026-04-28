/**
 * "My Tasks" view — shows all tasks and AR items owned by the current user.
 *
 * Items are displayed in a compact table grouped by status, priority, or
 * project (user-togglable).  Each row carries interactive QuickChips so the
 * user can change status, priority, ETA and owners without opening the full
 * TaskEditPopover.  Clicking the row title still opens the popover for note /
 * features edits.
 *
 * AR items (kind="ar") appear alongside regular tasks.  They show an amber
 * "AR" badge and a parent-task breadcrumb chip for context.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { TaskEditPopover } from "./TaskEditPopover";
import { StatusChip, PriorityChip, EtaChip, OwnersChips } from "./QuickChips";

// ── grouping helpers ──────────────────────────────────────────────────────────

type GroupBy = "status" | "priority" | "project";

const STATUS_ORDER = ["todo", "in-progress", "blocked", "done"] as const;
const PRIO_ORDER   = ["P0", "P1", "P2", "P3", ""]   as const;

const STATUS_HEADER: Record<string, string> = {
  "todo":        "bg-slate-50  border-slate-200  text-slate-700",
  "in-progress": "bg-yellow-50 border-yellow-200 text-yellow-800",
  "blocked":     "bg-rose-50   border-rose-200   text-rose-800",
  "done":        "bg-emerald-50 border-emerald-200 text-emerald-800",
};

const PRIO_HEADER: Record<string, string> = {
  P0: "bg-rose-50    border-rose-200    text-rose-800",
  P1: "bg-orange-50  border-orange-200  text-orange-800",
  P2: "bg-amber-50   border-amber-200   text-amber-800",
  P3: "bg-emerald-50 border-emerald-200 text-emerald-800",
  "": "bg-slate-50   border-slate-200   text-slate-500",
};

interface Group { key: string; label: string; headerCls: string; tasks: Task[] }

function groupTasks(tasks: Task[], by: GroupBy): Group[] {
  if (by === "status") {
    return STATUS_ORDER
      .map((s) => ({
        key: s, label: s,
        headerCls: STATUS_HEADER[s] ?? "bg-slate-50 border-slate-200 text-slate-600",
        tasks: tasks.filter((t) => t.status === s),
      }))
      .filter((g) => g.tasks.length > 0);
  }

  if (by === "priority") {
    return PRIO_ORDER
      .map((p) => ({
        key: p || "none", label: p || "(no priority)",
        headerCls: PRIO_HEADER[p] ?? "bg-slate-50 border-slate-200 text-slate-500",
        tasks: tasks.filter((t) => ((t.attrs.priority as string) ?? "") === p),
      }))
      .filter((g) => g.tasks.length > 0);
  }

  // by project
  const map = new Map<string, Task[]>();
  for (const t of tasks) {
    const p = t.projects[0] ?? "(no project)";
    if (!map.has(p)) map.set(p, []);
    map.get(p)!.push(t);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([label, tasks]) => ({
      key: label, label,
      headerCls: "bg-violet-50 border-violet-200 text-violet-800",
      tasks,
    }));
}

// ── TaskRow ───────────────────────────────────────────────────────────────────

function TaskRow({ task, onOpen }: { task: Task; onOpen: (t: Task) => void }) {
  const isAr = task.kind === "ar";

  return (
    <tr
      onClick={() => onOpen(task)}
      className="group hover:bg-sky-50/40 cursor-pointer border-b border-slate-100 last:border-0 transition-colors"
    >
      {/* Title + AR badge + parent breadcrumb + project/feature chips */}
      <td className="py-2.5 pl-4 pr-2 min-w-[200px]">
        <div className="flex flex-wrap items-center gap-1.5">
          {isAr && (
            <span
              title="Action Required item"
              className="chip text-[10px] font-bold bg-amber-50 border border-amber-300 text-amber-800 px-1.5 py-0.5"
            >
              AR
            </span>
          )}
          <span className="font-medium text-slate-800 text-sm leading-snug group-hover:text-sky-800 transition-colors">
            {task.title}
          </span>
        </div>

        {/* Parent breadcrumb for AR items / subtasks */}
        {task.parent_task_id != null && (task.parent_title || task.parent_uuid) && (
          <div className="flex items-center gap-1 mt-0.5">
            <span className="text-slate-300 text-[10px]">↑</span>
            {task.parent_uuid && (
              <span className="chip font-mono text-[10px] bg-slate-50 border border-slate-200 text-slate-400 px-1.5 py-0">
                {task.parent_uuid}
              </span>
            )}
            {task.parent_title && (
              <span className="text-[11px] text-slate-400 truncate max-w-[220px]">{task.parent_title}</span>
            )}
          </div>
        )}

        {(task.projects.length > 0 || task.features.length > 0) && (
          <div className="flex flex-wrap gap-1 mt-1">
            {task.projects.map((p) => (
              <span key={p} className="chip chip-project" style={{ fontSize: "10px" }}>#{p}</span>
            ))}
            {task.features.map((f) => (
              <span key={f} className="chip chip-feature" style={{ fontSize: "10px" }}>★{f}</span>
            ))}
          </div>
        )}
      </td>

      {/* Status — stop propagation so the chip dropdown doesn't open the popover */}
      <td className="py-2.5 px-2 whitespace-nowrap align-middle" onClick={(e) => e.stopPropagation()}>
        <StatusChip task={task} canWrite />
      </td>

      {/* Priority */}
      <td className="py-2.5 px-2 whitespace-nowrap align-middle" onClick={(e) => e.stopPropagation()}>
        <PriorityChip task={task} canWrite />
      </td>

      {/* ETA */}
      <td className="py-2.5 px-2 whitespace-nowrap align-middle" onClick={(e) => e.stopPropagation()}>
        <EtaChip task={task} canWrite />
      </td>

      {/* Owners */}
      <td className="py-2.5 pl-2 pr-4 align-middle" onClick={(e) => e.stopPropagation()}>
        <div className="flex flex-wrap gap-1">
          <OwnersChips task={task} canWrite />
        </div>
      </td>
    </tr>
  );
}

// ── GroupSection ──────────────────────────────────────────────────────────────

function GroupSection({ group, onOpen }: { group: Group; onOpen: (t: Task) => void }) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="rounded-lg border border-slate-200 overflow-hidden shadow-sm">
      {/* Group header */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className={`w-full flex items-center gap-2 px-4 py-2 border-b text-left ${group.headerCls} hover:opacity-90 transition-opacity`}
      >
        <span className="text-xs font-bold uppercase tracking-wider">{group.label}</span>
        <span className="text-xs opacity-60">{group.tasks.length} item{group.tasks.length === 1 ? "" : "s"}</span>
        <span className="ml-auto text-xs opacity-50">{collapsed ? "▸" : "▾"}</span>
      </button>

      {/* Table */}
      {!collapsed && (
        <table className="w-full">
          <thead className="sr-only">
            <tr>
              <th>Title</th>
              <th>Status</th>
              <th>Priority</th>
              <th>ETA</th>
              <th>Owners</th>
            </tr>
          </thead>
          <tbody>
            {group.tasks.map((t) => (
              <TaskRow key={t.id} task={t} onOpen={onOpen} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── MyTasksView ───────────────────────────────────────────────────────────────

export function MyTasksView() {
  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me(),
  });

  const [groupBy,  setGroupBy]  = useState<GroupBy>("status");
  const [hideDone, setHideDone] = useState(true);
  const [editing,  setEditing]  = useState<Task | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["my-tasks", me?.name, hideDone],
    queryFn: () =>
      me?.name
        ? api.tasks({ owner: me.name, hide_done: hideDone, top_level_only: false, include_children: false })
        : Promise.resolve(null),
    enabled: !!me?.name,
  });

  const tasks  = data?.tasks ?? [];
  const groups = useMemo(() => groupTasks(tasks, groupBy), [tasks, groupBy]);

  const totalOpen = tasks.filter((t) => t.status !== "done").length;
  const totalDone = tasks.filter((t) => t.status === "done").length;

  if (meLoading || isLoading) {
    return <div className="p-8 text-slate-400 text-sm">Loading…</div>;
  }

  if (!me) return null;

  return (
    <>
      <div className="p-4 space-y-4 max-w-5xl mx-auto">

        {/* ── Toolbar ── */}
        <div className="flex flex-wrap items-center gap-2">
          <div>
            <h1 className="text-xl font-semibold text-slate-800">My Tasks</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              Showing tasks & ARs owned by{" "}
              <span className="font-medium text-slate-700">@{me.name}</span>
              {" · "}
              <span className="text-slate-700 font-medium">{totalOpen}</span> open
              {!hideDone && (
                <> · <span className="text-slate-700 font-medium">{totalDone}</span> done</>
              )}
            </p>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {/* Group-by selector */}
            <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-0.5">
              <span className="text-xs text-slate-400 px-1.5">Group</span>
              {(["status", "priority", "project"] as GroupBy[]).map((g) => (
                <button
                  key={g}
                  onClick={() => setGroupBy(g)}
                  className={`text-xs rounded px-2 py-0.5 transition-colors font-medium
                    ${groupBy === g
                      ? "bg-white text-slate-800 shadow-sm"
                      : "text-slate-500 hover:text-slate-700"}`}
                >
                  {g}
                </button>
              ))}
            </div>

            {/* Hide done */}
            <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none rounded border border-slate-200 bg-slate-50 px-2 py-1">
              <input
                type="checkbox"
                checked={hideDone}
                onChange={(e) => setHideDone(e.target.checked)}
                className="rounded"
              />
              hide done
            </label>
          </div>
        </div>

        {/* ── Empty state ── */}
        {tasks.length === 0 && (
          <div className="rounded-xl border-2 border-dashed border-slate-200 p-12 text-center">
            <div className="text-2xl mb-2">✓</div>
            <div className="font-medium text-slate-600">No open tasks for @{me.name}</div>
            <div className="text-xs text-slate-400 mt-1">
              {hideDone
                ? "All done — or toggle 'hide done' to see completed tasks."
                : "No tasks assigned yet."}
            </div>
          </div>
        )}

        {/* ── Groups ── */}
        {groups.map((group) => (
          <GroupSection key={group.key} group={group} onOpen={setEditing} />
        ))}
      </div>

      {editing && <TaskEditPopover task={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
