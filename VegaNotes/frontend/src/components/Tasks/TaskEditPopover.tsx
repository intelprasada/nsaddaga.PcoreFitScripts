import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Task } from "../../api/client";

const STATUSES = ["todo", "in-progress", "blocked", "done"];
const PRIORITIES = ["", "P0", "P1", "P2", "P3"];

interface Props {
  task: Task;
  onClose: () => void;
}

/**
 * Modal for editing a single task's structured attributes.
 *
 * Fires one PATCH /api/tasks/{id} per save with only the fields the user
 * actually changed. The server enforces ownership (members can only edit
 * tasks they own) and round-trips the change through the underlying .md
 * file via `markdown_ops.replace_attr` / `replace_multi_attr`.
 *
 * Multi-valued fields (owners, features) are entered as comma-separated
 * lists and sent as full replacements — easier to reason about than diffs.
 */
export function TaskEditPopover({ task, onClose }: Props) {
  const qc = useQueryClient();
  const { data: knownUsers = [] } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.users(),
  });

  const initialPriority = (task.attrs.priority as string) ?? "";
  const initialEta = task.eta ?? "";
  const initialOwners = task.owners.join(", ");
  const initialFeatures = task.features.join(", ");
  const initialNotes = task.notes ?? "";

  const [status, setStatus] = useState(task.status);
  const [priority, setPriority] = useState(initialPriority);
  const [eta, setEta] = useState(initialEta);
  const [owners, setOwners] = useState(initialOwners);
  const [features, setFeatures] = useState(initialFeatures);
  const [notes, setNotes] = useState(initialNotes);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const splitCsv = (s: string) =>
    s.split(",").map((x) => x.trim()).filter(Boolean);

  const save = useMutation({
    mutationFn: () => {
      const patch: Record<string, unknown> = {};
      if (status !== task.status) patch.status = status;
      if (priority !== initialPriority) patch.priority = priority;
      if (eta !== initialEta) patch.eta = eta;
      const newOwners = splitCsv(owners);
      if (newOwners.join(",") !== task.owners.join(",")) patch.owners = newOwners;
      const newFeatures = splitCsv(features);
      if (newFeatures.join(",") !== task.features.join(",")) patch.features = newFeatures;
      if (notes !== initialNotes) patch.notes = notes;
      if (Object.keys(patch).length === 0) return Promise.resolve(task);
      return api.updateTask(task.id, patch);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["agenda"] });
      qc.invalidateQueries({ queryKey: ["note"] });
      qc.invalidateQueries({ queryKey: ["features"] });
      onClose();
    },
    onError: (e: any) => {
      // Prefer the typed ApiError carrying the server's `detail` field.
      if (e instanceof ApiError) {
        if (e.status === 403) {
          if (/no access to project/i.test(e.detail)) {
            setErr("Permission denied: you don't have access to this task's project. Ask an admin to add you to the project, or to add you as an @owner of this task.");
          } else if (/manager role/i.test(e.detail)) {
            setErr("Permission denied: this action requires the project manager role.");
          } else if (/own/i.test(e.detail)) {
            setErr("You can't edit this task — only its @owners (or a project manager / admin) can.");
          } else {
            setErr(`Permission denied: ${e.detail}`);
          }
        } else {
          setErr(`${e.status}: ${e.detail}`);
        }
      } else {
        setErr(String(e?.message ?? e));
      }
    },
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-[480px] max-w-[95vw] p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <div className="text-xs text-slate-500 font-mono">
              T-{task.id} · {task.kind}
            </div>
            <h3 className="font-semibold text-base mt-0.5">{task.title}</h3>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-lg leading-none"
            title="Close (Esc)"
          >
            ×
          </button>
        </div>

        <form
          className="space-y-3"
          onSubmit={(e) => { e.preventDefault(); setErr(null); save.mutate(); }}
        >
          <Field label="Status">
            <select className="border rounded px-2 py-1 text-sm w-full"
              value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>

          <Field label="Priority">
            <select className="border rounded px-2 py-1 text-sm w-full"
              value={priority} onChange={(e) => setPriority(e.target.value)}>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p || "(none)"}</option>)}
            </select>
          </Field>

          <Field label="ETA" hint="Intel WW (e.g. 2026-W18) or ISO date (2026-04-30). Empty to clear.">
            <input className="border rounded px-2 py-1 text-sm w-full font-mono"
              value={eta} onChange={(e) => setEta(e.target.value)}
              placeholder="2026-W18" />
          </Field>

          <Field label="Owners" hint={`Comma-separated. Known: ${knownUsers.join(", ") || "(none)"}`}>
            <input className="border rounded px-2 py-1 text-sm w-full"
              value={owners} onChange={(e) => setOwners(e.target.value)}
              placeholder="alice, bob" list="known-users" />
            <datalist id="known-users">
              {knownUsers.map((u) => <option key={u} value={u} />)}
            </datalist>
          </Field>

          <Field label="Features" hint="Comma-separated.">
            <input className="border rounded px-2 py-1 text-sm w-full"
              value={features} onChange={(e) => setFeatures(e.target.value)}
              placeholder="auth, billing" />
          </Field>

          <Field label="Notes" hint="Free-form per-task notes. Stored as `#note` continuation lines under the task in the .md file. Newlines = separate `#note` entries.">
            <textarea
              className="border rounded px-2 py-1 text-sm w-full font-mono"
              rows={4}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. blocked on review from @alice; retry after WW19 sync"
            />
          </Field>

          {err && <div className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{err}</div>}

          <div className="flex justify-end gap-2 pt-2 border-t">
            <button type="button" onClick={onClose}
              className="rounded border px-3 py-1 text-sm">cancel</button>
            <button type="submit" disabled={save.isPending}
              className="rounded bg-sky-600 text-white px-3 py-1 text-sm disabled:opacity-50">
              {save.isPending ? "saving…" : "save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-slate-600 mb-0.5">{label}</div>
      {children}
      {hint && <div className="text-[11px] text-slate-400 mt-0.5">{hint}</div>}
    </label>
  );
}
