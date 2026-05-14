/**
 * BulkEditBar — sticky toolbar for multi-row bulk edits in the Tasks table.
 *
 * Issue #33. Selection is managed by the parent (MyTasksView) and keyed by
 * stable refs (task_uuid when available, else String(id)) so it survives
 * react-query refetches and DB rebuilds.
 *
 * Concurrency model (per rubber-duck #33):
 *   - The backend's PATCH /tasks/{ref} does RMW under a per-file lock and
 *     returns 409 stale_task if note.body_md cached value disagrees with
 *     disk. Two parallel PATCHes against tasks in the *same .md file* will
 *     reliably collide.
 *   - Therefore we bucket by note_id and run **sequentially within a note,
 *     parallel across notes**.
 *
 * Features (and owners) on the backend are full-replacement, not deltas.
 * For "add" / "remove" actions we compute the next value per-task (union
 * for add, difference for remove) so we don't clobber per-task state.
 */

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";

const STATUSES   = ["todo", "in-progress", "blocked", "done"] as const;
const PRIORITIES = ["", "P0", "P1", "P2", "P3"] as const;

export type BulkAction =
  | { kind: "status";   status: string }
  | { kind: "priority"; priority: string }
  | { kind: "eta";      eta: string }
  | { kind: "owners";   owners: string[] }                // replace
  | { kind: "features"; mode: "add" | "remove"; values: string[] };

interface Failure {
  ref: string;
  title: string;
  reason: string;
}

interface Progress {
  done: number;
  total: number;
  failures: Failure[];
}

function refOf(t: Task): string {
  return t.task_uuid ?? String(t.id);
}

/** Backend PATCH owners/features are full replacements. Compute next value per task. */
function patchForTask(t: Task, action: BulkAction): Record<string, unknown> {
  switch (action.kind) {
    case "status":   return { status: action.status };
    case "priority": return { priority: action.priority };
    case "eta":      return { eta: action.eta };
    case "owners":   return { owners: action.owners };
    case "features": {
      const cur = t.features ?? [];
      if (action.mode === "add") {
        const set = new Set(cur);
        for (const v of action.values) set.add(v);
        return { features: Array.from(set) };
      }
      const drop = new Set(action.values);
      return { features: cur.filter((f) => !drop.has(f)) };
    }
  }
}

/** Apply `action` to `tasks`. Sequential within a note_id bucket, parallel across notes. */
async function runBulk(
  tasks: Task[],
  action: BulkAction,
  onProgress: (p: Progress) => void,
): Promise<Failure[]> {
  const buckets = new Map<number, Task[]>();
  for (const t of tasks) {
    const k = t.note_id ?? -1;
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k)!.push(t);
  }

  const total = tasks.length;
  let done = 0;
  const failures: Failure[] = [];

  await Promise.all(
    Array.from(buckets.values()).map(async (bucket) => {
      for (const t of bucket) {
        try {
          await api.updateTask(refOf(t), patchForTask(t, action));
        } catch (e: any) {
          let reason = e?.message ?? String(e);
          // Try to extract status code from "HTTP 409: ..." style
          const m = /\b(\d{3})\b/.exec(reason);
          if (m) reason = `${m[1]} ${reason.slice(0, 80)}`;
          failures.push({ ref: refOf(t), title: t.title, reason });
        } finally {
          done += 1;
          onProgress({ done, total, failures: [...failures] });
        }
      }
    }),
  );

  return failures;
}

interface Props {
  selectedTasks: Task[];
  onClear: () => void;
  /** Called with the refs of tasks that succeeded so the parent can drop them from selection. */
  onApplied: (succeededRefs: string[]) => void;
}

type ActiveEditor = null | "status" | "priority" | "eta" | "owners" | "features";

export function BulkEditBar({ selectedTasks, onClear, onApplied }: Props) {
  const qc = useQueryClient();
  const [editor, setEditor] = useState<ActiveEditor>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [lastFailures, setLastFailures] = useState<Failure[] | null>(null);

  // Per-action input state.
  const [statusVal, setStatusVal]   = useState("todo");
  const [priorityVal, setPriorityVal] = useState("");
  const [etaVal, setEtaVal]         = useState("");
  const [ownersVal, setOwnersVal]   = useState("");
  const [featuresVal, setFeaturesVal] = useState("");
  const [featureMode, setFeatureMode] = useState<"add" | "remove">("add");

  const count = selectedTasks.length;
  const ownerSummary = useMemo(() => {
    const s = new Set<string>();
    for (const t of selectedTasks) for (const o of t.owners) s.add(o);
    return Array.from(s).sort().join(", ");
  }, [selectedTasks]);

  if (count === 0) return null;

  async function apply(action: BulkAction) {
    if (running) return;
    setRunning(true);
    setLastFailures(null);
    setProgress({ done: 0, total: count, failures: [] });
    const failures = await runBulk(selectedTasks, action, setProgress);
    setRunning(false);
    setEditor(null);

    // Invalidate every derived view that might show task state.
    qc.invalidateQueries({ queryKey: ["my-tasks"] });
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["agenda"] });
    qc.invalidateQueries({ queryKey: ["note"] });
    qc.invalidateQueries({ queryKey: ["features"] });

    const failedRefs = new Set(failures.map((f) => f.ref));
    const succeededRefs = selectedTasks
      .map(refOf)
      .filter((r) => !failedRefs.has(r));
    onApplied(succeededRefs);

    setLastFailures(failures);
    // Clear progress after a beat so the UI doesn't linger on 100%.
    setTimeout(() => setProgress(null), 1500);
  }

  const baseBtn = "text-xs rounded px-2.5 py-1 font-medium transition-colors disabled:opacity-50";

  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 max-w-3xl w-[calc(100%-2rem)]">
      <div className="rounded-xl border border-slate-300 bg-white shadow-lg p-3 space-y-2">
        {/* Header row: count + actions */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-700">
            {count} task{count === 1 ? "" : "s"} selected
          </span>
          {ownerSummary && (
            <span className="text-xs text-slate-400 truncate max-w-[280px]" title={ownerSummary}>
              · @{ownerSummary.split(", ").join(" @")}
            </span>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-1">
            <button
              disabled={running}
              onClick={() => setEditor(editor === "status" ? null : "status")}
              className={`${baseBtn} ${editor === "status" ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              Set status
            </button>
            <button
              disabled={running}
              onClick={() => setEditor(editor === "priority" ? null : "priority")}
              className={`${baseBtn} ${editor === "priority" ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              Set priority
            </button>
            <button
              disabled={running}
              onClick={() => setEditor(editor === "eta" ? null : "eta")}
              className={`${baseBtn} ${editor === "eta" ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              Set ETA
            </button>
            <button
              disabled={running}
              onClick={() => setEditor(editor === "owners" ? null : "owners")}
              className={`${baseBtn} ${editor === "owners" ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              Reassign owners
            </button>
            <button
              disabled={running}
              onClick={() => setEditor(editor === "features" ? null : "features")}
              className={`${baseBtn} ${editor === "features" ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
            >
              Edit features
            </button>
            <button
              disabled={running}
              onClick={onClear}
              className="text-xs rounded px-2 py-1 text-slate-500 hover:text-slate-700 hover:bg-slate-100"
              title="Clear selection (Esc)"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Editor row */}
        {editor === "status" && (
          <div className="flex items-center gap-2">
            <select
              value={statusVal}
              onChange={(e) => setStatusVal(e.target.value)}
              className="text-xs border border-slate-300 rounded px-2 py-1"
            >
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button
              disabled={running}
              onClick={() => apply({ kind: "status", status: statusVal })}
              className={`${baseBtn} bg-sky-600 text-white hover:bg-sky-700`}
            >
              Apply to {count}
            </button>
          </div>
        )}

        {editor === "priority" && (
          <div className="flex items-center gap-2">
            <select
              value={priorityVal}
              onChange={(e) => setPriorityVal(e.target.value)}
              className="text-xs border border-slate-300 rounded px-2 py-1"
            >
              {PRIORITIES.map((p) => <option key={p} value={p}>{p || "(none)"}</option>)}
            </select>
            <button
              disabled={running}
              onClick={() => apply({ kind: "priority", priority: priorityVal })}
              className={`${baseBtn} bg-sky-600 text-white hover:bg-sky-700`}
            >
              Apply to {count}
            </button>
          </div>
        )}

        {editor === "eta" && (
          <div className="flex items-center gap-2">
            <input
              value={etaVal}
              onChange={(e) => setEtaVal(e.target.value)}
              placeholder="ww20  /  2026-05-15  /  (empty to clear)"
              className="text-xs border border-slate-300 rounded px-2 py-1 flex-1 min-w-[200px]"
            />
            <button
              disabled={running}
              onClick={() => apply({ kind: "eta", eta: etaVal })}
              className={`${baseBtn} bg-sky-600 text-white hover:bg-sky-700`}
            >
              Apply to {count}
            </button>
          </div>
        )}

        {editor === "owners" && (
          <div className="flex items-center gap-2">
            <input
              value={ownersVal}
              onChange={(e) => setOwnersVal(e.target.value)}
              placeholder="alice, bob   (REPLACES every task's owners)"
              className="text-xs border border-slate-300 rounded px-2 py-1 flex-1 min-w-[200px]"
            />
            <button
              disabled={running}
              onClick={() =>
                apply({
                  kind: "owners",
                  owners: ownersVal
                    .split(/[,\s]+/)
                    .map((o) => o.trim().replace(/^@/, ""))
                    .filter(Boolean),
                })
              }
              className={`${baseBtn} bg-sky-600 text-white hover:bg-sky-700`}
            >
              Replace on {count}
            </button>
          </div>
        )}

        {editor === "features" && (
          <div className="flex items-center gap-2">
            <select
              value={featureMode}
              onChange={(e) => setFeatureMode(e.target.value as "add" | "remove")}
              className="text-xs border border-slate-300 rounded px-2 py-1"
            >
              <option value="add">Add</option>
              <option value="remove">Remove</option>
            </select>
            <input
              value={featuresVal}
              onChange={(e) => setFeaturesVal(e.target.value)}
              placeholder="feature1, feature2"
              className="text-xs border border-slate-300 rounded px-2 py-1 flex-1 min-w-[200px]"
            />
            <button
              disabled={running}
              onClick={() => {
                const values = featuresVal.split(/[,\s]+/).map((v) => v.trim()).filter(Boolean);
                if (values.length === 0) return;
                apply({ kind: "features", mode: featureMode, values });
              }}
              className={`${baseBtn} bg-sky-600 text-white hover:bg-sky-700`}
            >
              {featureMode === "add" ? "Add to" : "Remove from"} {count}
            </button>
          </div>
        )}

        {/* Progress + last-run summary */}
        {progress && (
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-sky-500 transition-[width] duration-150"
                style={{ width: `${(progress.done / progress.total) * 100}%` }}
              />
            </div>
            <span className="text-[11px] text-slate-500 tabular-nums">
              {progress.done}/{progress.total}
            </span>
          </div>
        )}

        {lastFailures && lastFailures.length > 0 && !progress && (
          <div className="text-[11px] text-rose-700 bg-rose-50 border border-rose-200 rounded px-2 py-1.5">
            <span className="font-semibold">{lastFailures.length} failed</span>
            {" — "}
            {lastFailures.slice(0, 3).map((f, i) => (
              <span key={f.ref}>
                {i > 0 && "; "}
                <span title={f.reason}>{f.title || f.ref}</span>
              </span>
            ))}
            {lastFailures.length > 3 && ` and ${lastFailures.length - 3} more`}
            <span className="text-rose-500"> · failed rows kept selected</span>
          </div>
        )}

        {lastFailures && lastFailures.length === 0 && !progress && (
          <div className="text-[11px] text-emerald-700">All updates applied successfully.</div>
        )}
      </div>
    </div>
  );
}
