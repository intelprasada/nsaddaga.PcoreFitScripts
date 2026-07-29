import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { TaskEditPopover } from "../Tasks/TaskEditPopover";
import { NewTaskComposer } from "../Tasks/NewTaskComposer";
import { useUI, filtersToParams } from "../../store/ui";
import { useFontScale, type FontScale } from "../../store/fontScale";
import { loadDoneScope, saveDoneScope, type DoneScope } from "../../store/doneScope";
import { KanbanEmailModal } from "./KanbanEmailModal";
import {
  parseProgressValue, progressColor, type ProgressColor,
} from "../../lib/progressChip";

const SCALES: { value: FontScale; label: string; title: string }[] = [
  { value: "sm", label: "A",  title: "Small text"  },
  { value: "md", label: "A",  title: "Medium text" },
  { value: "lg", label: "A",  title: "Large text"  },
];

const STATUS_COLUMNS = ["todo", "in-progress", "blocked", "done"] as const;
// #320: alternate grouping — one column per progress-color band, plus a
// leading `no-progress` bucket for tasks that don't carry the token.
const PROGRESS_COLUMNS = [
  "no-progress", "red", "amber", "green", "gold", "blue",
] as const;

type GroupMode = "status" | "progress";

const PROGRESS_COLUMN_LABELS: Record<string, string> = {
  "no-progress": "no #progress",
  red: "< 25%",
  amber: "25-74%",
  green: "75-99%",
  gold: "≥ 100%",
  blue: "counter",
};

function progressBucketFor(t: Task): string {
  const raw = t.attrs?.progress;
  if (raw == null) return "no-progress";
  const first = Array.isArray(raw) ? raw[0] : String(raw);
  const p = parseProgressValue(first ?? "");
  if (!p) return "no-progress";
  return progressColor(p) as ProgressColor;
}

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
  const [composerColumn, setComposerColumn] = useState<string | null>(null);
  const [emailOpen, setEmailOpen] = useState(false);
  const { scale, setScale } = useFontScale();
  // #320: swim-lane group mode. Persists in localStorage.
  const [groupMode, setGroupMode] = useState<GroupMode>(() => {
    try {
      const v = localStorage.getItem("vega:kanban:groupMode");
      return v === "progress" ? "progress" : "status";
    } catch {
      return "status";
    }
  });
  const updateGroupMode = (m: GroupMode) => {
    setGroupMode(m);
    try { localStorage.setItem("vega:kanban:groupMode", m); } catch { /* ignore */ }
  };
  // #258: scope done tasks to non-archived notes by default. Persists per
  // view in localStorage; flipping in MyTasks doesn't change Kanban.
  const [doneScope, setDoneScope] = useState<DoneScope>(() => loadDoneScope("kanban"));
  const updateDoneScope = (v: DoneScope) => {
    setDoneScope(v);
    saveDoneScope("kanban", v);
  };
  const { data } = useQuery({
    queryKey: ["tasks", filters, "kanban", doneScope],
    queryFn: () =>
      api.tasks({
        ...filtersToParams(filters),
        hide_done: false,
        top_level_only: true,
        include_children: true,
        done_scope: doneScope,
      }),
  });
  const tasks = data?.tasks ?? [];
  const columns = groupMode === "progress" ? PROGRESS_COLUMNS : STATUS_COLUMNS;
  const grouped = useMemo(() => {
    const g: Record<string, Task[]> = {};
    for (const c of columns) g[c] = [];
    if (groupMode === "progress") {
      for (const t of tasks) {
        const b = progressBucketFor(t);
        (g[b] ?? g["no-progress"]).push(t);
      }
    } else {
      for (const t of tasks) (g[t.status] ?? g.todo).push(t);
    }
    return g;
  }, [tasks, groupMode, columns]);

  return (
    <>
      {/* toolbar */}
      <div className="flex items-center justify-end gap-1 px-4 pt-3 pb-1">
        {/* #320: swim-lane group mode. */}
        <label
          className="mr-2 flex items-center gap-1.5 text-xs text-slate-600 select-none rounded border border-slate-200 bg-slate-50 px-2 py-1"
          title="Choose how to group cards into columns. `Status` is the default (todo/in-progress/blocked/done). `Progress` buckets cards by their #progress color band (red < 25%, amber 25-74%, green 75-99%, gold ≥ 100%, blue = counter, plus 'no #progress' for tasks that don't carry the token)."
        >
          <span className="text-slate-500">Group by</span>
          <select
            value={groupMode}
            onChange={(e) => updateGroupMode(e.target.value as GroupMode)}
            className="bg-white border border-slate-300 rounded text-xs px-1 py-0.5"
          >
            <option value="status">status</option>
            <option value="progress">progress</option>
          </select>
        </label>
        <label
          className="mr-2 flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none rounded border border-slate-200 bg-slate-50 px-2 py-1"
          title="When on, the Done column shows only tasks from active (non-archived) notes. Turn off to include done tasks from archived weekly files."
        >
          <input
            type="checkbox"
            checked={doneScope === "active"}
            onChange={(e) => updateDoneScope(e.target.checked ? "active" : "all")}
            className="rounded"
          />
          done from active files only
        </label>
        <button
          onClick={() => setEmailOpen(true)}
          title="Send a snapshot of the current Kanban view via email"
          className="mr-3 px-2 py-1 text-xs rounded border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
        >
          ✉ Send Email
        </button>
        <span className="text-xs text-slate-400 mr-1">Card text</span>
        {SCALES.map(({ value, label, title }) => (
          <button
            key={value}
            title={title}
            onClick={() => setScale(value)}
            className={`w-7 h-7 rounded flex items-center justify-center font-medium transition-colors
              ${value === "sm" ? "text-[11px]" : value === "md" ? "text-[13px]" : "text-[16px]"}
              ${scale === value
                ? "bg-slate-700 text-white"
                : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="flex gap-3 px-4 pb-4 overflow-x-auto">
        {columns.map((c) => (
          <div key={c} className="flex-1 min-w-[260px] rounded-lg p-3 bg-slate-100">
            <div className="text-xs uppercase tracking-wide text-slate-500 mb-2 flex items-center justify-between">
              <span>
                {groupMode === "progress" ? (PROGRESS_COLUMN_LABELS[c] ?? c) : c}
              </span>
              <span className="flex items-center gap-1">
                <span>{grouped[c].length}</span>
                {/* #320: `+` composer only makes sense in status mode; a
                    new task can't be placed into a specific progress
                    bucket up-front (the bucket derives from #progress). */}
                {groupMode === "status" && (
                  <button
                    onClick={() => setComposerColumn(composerColumn === c ? null : c)}
                    title={`Add task to ${c}`}
                    className="w-5 h-5 rounded text-slate-400 hover:text-sky-700 hover:bg-white flex items-center justify-center text-base leading-none"
                  >
                    +
                  </button>
                )}
              </span>
            </div>
            {groupMode === "status" && composerColumn === c && (
              <div className="mb-2">
                <NewTaskComposer
                  defaultStatus={c}
                  defaultProject={filters.project}
                  onClose={() => setComposerColumn(null)}
                  compact
                />
              </div>
            )}
            <div className="space-y-2">
              {grouped[c].map((t) => (
                <TaskCard key={t.id} task={t} onOpen={setEditing} />
              ))}
            </div>
          </div>
        ))}
      </div>
      {editing && <TaskEditPopover task={editing} onClose={() => setEditing(null)} />}
      {emailOpen && (
        <KanbanEmailModal
          tasks={tasks}
          grouped={grouped}
          columns={columns}
          filters={filters}
          onClose={() => setEmailOpen(false)}
        />
      )}
    </>
  );
}
