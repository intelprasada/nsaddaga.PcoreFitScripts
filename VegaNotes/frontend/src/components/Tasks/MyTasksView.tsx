/**
 * "My Tasks" view — shows top-level tasks owned by the current user, with
 * any AR items rendered as a collapsible dropdown inside each row (matching
 * the Kanban TaskCard UX).
 *
 * Items are displayed in a compact table grouped by status, priority, or
 * project (user-togglable).  Each row carries interactive QuickChips so the
 * user can change status, priority, ETA and owners without opening the full
 * TaskEditPopover.  Clicking the row title still opens the popover for note /
 * features edits.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, type ChildTask, type Task } from "../../api/client";
import { formatIntelWw } from "@veganotes/parser";
import { TaskEditPopover } from "./TaskEditPopover";
import { NewTaskComposer } from "./NewTaskComposer";
import { StatusChip, PriorityChip, EtaChip, OwnersChips } from "./QuickChips";
import { useUI } from "../../store/ui";

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

interface Group {
  key: string; label: string; headerCls: string; tasks: Task[];
  /** Defaults for the per-group "+" composer based on grouping axis. */
  composerDefaults: { status?: string; priority?: string; project?: string };
}

function groupTasks(tasks: Task[], by: GroupBy): Group[] {
  if (by === "status") {
    return STATUS_ORDER
      .map((s) => ({
        key: s, label: s,
        headerCls: STATUS_HEADER[s] ?? "bg-slate-50 border-slate-200 text-slate-600",
        tasks: tasks.filter((t) => t.status === s),
        composerDefaults: { status: s },
      }))
      .filter((g) => g.tasks.length > 0);
  }

  if (by === "priority") {
    return PRIO_ORDER
      .map((p) => ({
        key: p || "none", label: p || "(no priority)",
        headerCls: PRIO_HEADER[p] ?? "bg-slate-50 border-slate-200 text-slate-500",
        tasks: tasks.filter((t) => ((t.attrs.priority as string) ?? "") === p),
        composerDefaults: { priority: p || undefined },
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
      composerDefaults: { project: label === "(no project)" ? undefined : label },
    }));
}

// ── AR helpers (mirrors Card/TaskCard) ────────────────────────────────────────

const AR_NEXT: Record<string, string> = {
  todo: "in-progress",
  "in-progress": "done",
  done: "todo",
  blocked: "todo",
};

function etaLabel(eta: string | null | undefined): string {
  if (!eta) return "";
  if (/^\d{4}-\d{2}-\d{2}/.test(eta)) {
    try { return formatIntelWw(eta.slice(0, 10)); } catch { return eta; }
  }
  return eta;
}

function ArRow({ ar, onCycle }: { ar: ChildTask; onCycle: () => void }) {
  const done = ar.status === "done";
  const statusLabel = ar.status === "in-progress" ? "wip" : ar.status;
  const bubbleColor =
    ar.status === "done"        ? "bg-emerald-100 text-emerald-800" :
    ar.status === "in-progress" ? "bg-yellow-100  text-yellow-800"  :
    ar.status === "blocked"     ? "bg-rose-100    text-rose-800"    :
                                  "bg-slate-100   text-slate-600";
  return (
    <li className="flex items-center gap-2 text-xs">
      <button
        onClick={(e) => { e.stopPropagation(); onCycle(); }}
        title={`Status: ${ar.status} — click to cycle`}
        className={`chip px-2 py-0.5 cursor-pointer font-medium ${bubbleColor}`}
      >
        {statusLabel}
      </button>
      <span className={done ? "line-through text-slate-400 font-medium" : "text-slate-700 font-medium"}>
        {ar.title}
      </span>
      {ar.eta && <span className="chip chip-eta text-[10px]" title={ar.eta}>{etaLabel(ar.eta)}</span>}
    </li>
  );
}

// ── TaskRow ───────────────────────────────────────────────────────────────────

function TaskRow({ task, onOpen }: { task: Task; onOpen: (t: Task) => void }) {
  const ars = (task.children ?? []).filter((c) => c.kind === "ar");
  const arDone = ars.filter((a) => a.status === "done").length;
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();

  const cycleAr = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api.updateTask(id, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
      qc.invalidateQueries({ queryKey: ["agenda"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
  });

  return (
    <>
      <tr
        onClick={() => onOpen(task)}
        className="group hover:bg-sky-50/40 cursor-pointer border-b border-slate-100 transition-colors"
      >
        {/* Title + project/feature chips + AR toggle */}
        <td className="py-2.5 pl-4 pr-2 min-w-[200px]">
          <div className="font-medium text-slate-800 text-sm leading-snug group-hover:text-sky-800 transition-colors">
            {task.title}
          </div>
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
          {ars.length > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
              className="mt-1.5 text-xs text-amber-800 hover:text-amber-900 flex items-center gap-1.5 bg-amber-50 border border-amber-200 rounded px-2 py-0.5"
              title="Action Required items (subtasks declared with !AR)"
            >
              <span>{expanded ? "▾" : "▸"}</span>
              <span className="font-bold">{ars.length} AR{ars.length === 1 ? "" : "s"}</span>
              <span className="text-slate-600">({arDone} done / {ars.length - arDone} open)</span>
            </button>
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

      <AnimatePresence initial={false}>
        {expanded && ars.length > 0 && (
          <tr className="border-b border-slate-100">
            <td colSpan={5} className="py-0 pl-6 pr-4 bg-amber-50/30">
              <motion.ul
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="my-2 ml-2 border-l-2 border-amber-300 pl-3 space-y-1.5 overflow-hidden"
              >
                {ars.map((a) => (
                  <ArRow
                    key={a.id}
                    ar={a}
                    onCycle={() => cycleAr.mutate({ id: a.id, status: AR_NEXT[a.status] ?? "in-progress" })}
                  />
                ))}
              </motion.ul>
            </td>
          </tr>
        )}
      </AnimatePresence>
    </>
  );
}

// ── GroupSection ──────────────────────────────────────────────────────────────

function GroupSection({
  group, onOpen, project, composerOpen, onComposerToggle,
}: {
  group: Group;
  onOpen: (t: Task) => void;
  project?: string;
  composerOpen: boolean;
  onComposerToggle: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="rounded-lg border border-slate-200 overflow-hidden shadow-sm">
      {/* Group header */}
      <div className={`w-full flex items-center gap-2 px-4 py-2 border-b ${group.headerCls}`}>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-2 flex-1 text-left hover:opacity-90 transition-opacity"
        >
          <span className="text-xs font-bold uppercase tracking-wider">{group.label}</span>
          <span className="text-xs opacity-60">{group.tasks.length} task{group.tasks.length === 1 ? "" : "s"}</span>
          <span className="ml-auto text-xs opacity-50">{collapsed ? "▸" : "▾"}</span>
        </button>
        <button
          onClick={onComposerToggle}
          title={`Add task to ${group.label}`}
          className="w-5 h-5 rounded text-current opacity-50 hover:opacity-100 hover:bg-white/60 flex items-center justify-center text-base leading-none"
        >
          +
        </button>
      </div>

      {composerOpen && (
        <div className="p-2 bg-slate-50 border-b border-slate-200">
          <NewTaskComposer
            defaultStatus={group.composerDefaults.status ?? "todo"}
            defaultProject={group.composerDefaults.project ?? project}
            defaultPriority={group.composerDefaults.priority}
            onClose={onComposerToggle}
            compact
          />
        </div>
      )}

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
  // Single-active composer: either a group key (per-group "+") or "__top".
  const [composerOpen, setComposerOpen] = useState<string | null>(null);
  const { filters } = useUI();

  const { data, isLoading } = useQuery({
    queryKey: ["my-tasks", me?.name, hideDone],
    queryFn: () =>
      me?.name
        ? api.tasks({ owner: me.name, hide_done: hideDone, top_level_only: true, include_children: true })
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
              Showing tasks owned by{" "}
              <span className="font-medium text-slate-700">@{me.name}</span>
              {" · "}
              <span className="text-slate-700 font-medium">{totalOpen}</span> open
              {!hideDone && (
                <> · <span className="text-slate-700 font-medium">{totalDone}</span> done</>
              )}
            </p>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {/* Global "+ New task" — always available, prefills status=todo */}
            <button
              onClick={() => setComposerOpen(composerOpen === "__top" ? null : "__top")}
              className="text-xs rounded bg-sky-600 text-white px-2.5 py-1 hover:bg-sky-700 font-medium"
              title="Add a new task"
            >
              + New task
            </button>

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

        {composerOpen === "__top" && (
          <NewTaskComposer
            defaultStatus="todo"
            defaultProject={filters.project}
            onClose={() => setComposerOpen(null)}
          />
        )}

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
          <GroupSection
            key={group.key}
            group={group}
            onOpen={setEditing}
            project={filters.project}
            composerOpen={composerOpen === group.key}
            onComposerToggle={() =>
              setComposerOpen(composerOpen === group.key ? null : group.key)
            }
          />
        ))}
      </div>

      {editing && <TaskEditPopover task={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
