import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type ChildTask, type Task } from "../../api/client";
import { TitleWithBreakHints } from "../../lib/titleWrap";
import { extraTagChips } from "../../lib/tagChips";
import { nextArStatus, AR_STATUS_STYLES } from "./arStatus";

const STATUSES = ["todo", "in-progress", "blocked", "done"];
const PRIORITIES = ["", "P0", "P1", "P2", "P3"];

interface Props {
  task: Task;
  onClose: () => void;
}

/**
 * Modal for editing a single task (or one of its ARs).
 *
 * Fires one PATCH /api/tasks/{id} per save with only the fields the user
 * actually changed. The server enforces ownership (members can only edit
 * tasks they own) and round-trips the change through the underlying .md
 * file via `markdown_ops.replace_attr` / `replace_multi_attr` /
 * `replace_task_title`.
 *
 * ── Navigation (issue #283) ──────────────────────────────────────────
 * The user can click the ✎ pencil on an AR row to swap the popover
 * contents to that AR (with a breadcrumb back to the parent). This
 * gives ARs full edit affordances without introducing a second modal
 * or a nested popover.
 */
export function TaskEditPopover({ task: initialTask, onClose }: Props) {
  const initialRef: string | number = initialTask.task_uuid ?? initialTask.id;
  const [activeRef, setActiveRef] = useState<string | number>(initialRef);
  const isRoot = activeRef === initialRef;

  // Close on Esc (issue #281). Registered at document level so it fires
  // regardless of focus (input, textarea, or outside the panel).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        // If we've drilled into an AR, first Esc pops back to parent.
        // Second Esc (or Esc on the root task) closes the popover.
        if (!isRoot) {
          setActiveRef(initialRef);
        } else {
          onClose();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose, isRoot, initialRef]);

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-black/30"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Edit task T-${initialTask.id}${initialTask.title ? `: ${initialTask.title}` : ""}`}
    >
      <div className="min-h-full flex items-start sm:items-center justify-center p-4">
        <div
          className="bg-white rounded-lg shadow-xl w-[480px] max-w-[95vw]
                     max-h-[calc(100dvh-2rem)] flex flex-col overflow-hidden my-4"
          onClick={(e) => e.stopPropagation()}
        >
          {/*
           * `key` on the inner component forces a remount on AR swap so
           * every piece of form state (owners, status, notes, err…) is
           * reset from scratch to the freshly-fetched task's values.
           */}
          <PopoverContent
            key={String(activeRef)}
            activeRef={activeRef}
            initialTask={isRoot ? initialTask : undefined}
            parentTask={isRoot ? null : initialTask}
            onSwapToAr={(ref) => setActiveRef(ref)}
            onBack={() => setActiveRef(initialRef)}
            onClose={onClose}
          />
        </div>
      </div>
    </div>
  );
}

interface PopoverContentProps {
  activeRef: string | number;
  /** When we're on the root task, the caller already has the full Task —
   * pass it through directly to avoid a redundant network round-trip. */
  initialTask: Task | undefined;
  /** When we've drilled into an AR, the parent Task is used to render the
   * breadcrumb. `null` on the root. */
  parentTask: Task | null;
  onSwapToAr: (ref: string | number) => void;
  onBack: () => void;
  onClose: () => void;
}

function PopoverContent({
  activeRef, initialTask, parentTask, onSwapToAr, onBack, onClose,
}: PopoverContentProps) {
  // Always subscribe to the task via useQuery so mutations that
  // invalidate the ["task", ...] key (add/delete AR, patch title,
  // patch fields, delete task, …) drive a refetch and the popover
  // stays in sync with disk. `initialData` seeds the first render
  // with the prop the caller already has so there's no loading
  // flash on the root task view. On the AR-swap path `initialTask`
  // is undefined, so the query fetches on mount just like before.
  //
  // `PopoverContent` is keyed on `activeRef` in the parent, so it
  // remounts cleanly on AR navigation and `initialData` is applied
  // fresh each time.
  //
  // See issue #287 — without this, adding or deleting an AR from
  // the popover only showed up after close-and-reopen.
  const { data: task, isLoading, error: fetchError } = useQuery({
    queryKey: ["task", String(activeRef), "with-children"],
    queryFn: () => api.getTask(activeRef, { includeChildren: true }),
    initialData: initialTask,
    staleTime: 0,
  });

  if (!task) {
    return (
      <>
        {parentTask && (
          <Breadcrumb parent={parentTask} onBack={onBack} onClose={onClose} />
        )}
        <div className="p-8 text-center text-sm text-slate-500">
          {isLoading ? "Loading…" : fetchError
            ? `Failed to load: ${(fetchError as Error).message}`
            : "No data."}
        </div>
      </>
    );
  }

  return (
    <PopoverForm
      task={task}
      parentTask={parentTask}
      onSwapToAr={onSwapToAr}
      onBack={onBack}
      onClose={onClose}
    />
  );
}

interface PopoverFormProps {
  task: Task;
  parentTask: Task | null;
  onSwapToAr: (ref: string | number) => void;
  onBack: () => void;
  onClose: () => void;
}

function PopoverForm({
  task, parentTask, onSwapToAr, onBack, onClose,
}: PopoverFormProps) {
  const qc = useQueryClient();
  // #312: scope suggestions to users with tasks in this task's project.
  // Tasks with no project fall back to the global user list.
  const taskProject = task.projects?.[0];
  const { data: knownUsers = [] } = useQuery({
    queryKey: ["users", taskProject ?? null],
    queryFn: () => api.users(taskProject),
  });

  const initialPriority = (task.attrs.priority as string) ?? "";
  const initialEta = task.eta ?? "";
  const initialOwners = task.owners.join(", ");
  const initialFeatures = task.features.join(", ");
  // #314: link tokens live in task.attrs (multi-valued strings). Join with
  // commas for the CSV-style editor pattern used elsewhere in this popover.
  const attrCsv = (key: string): string => {
    const v = task.attrs[key];
    if (!v) return "";
    return (Array.isArray(v) ? v : [v]).join(", ");
  };
  const initialHsd = attrCsv("hsd");
  const initialJira = attrCsv("jira");
  const initialPr = attrCsv("pr");
  const initialUrl = attrCsv("url");
  const noteHistory = task.note_history ?? (task.notes ? task.notes.split("\n").filter(Boolean) : []);

  const [status, setStatus] = useState(task.status);
  const [priority, setPriority] = useState(initialPriority);
  const [eta, setEta] = useState(initialEta);
  const [owners, setOwners] = useState(initialOwners);
  const [features, setFeatures] = useState(initialFeatures);
  // #314
  const [hsd, setHsd] = useState(initialHsd);
  const [jira, setJira] = useState(initialJira);
  const [pr, setPr] = useState(initialPr);
  const [urlField, setUrlField] = useState(initialUrl);
  const [newNote, setNewNote] = useState("");
  const [newArTitle, setNewArTitle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDeleteArId, setConfirmDeleteArId] = useState<number | null>(null);

  // Parent-title inline pencil (issue #283).
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(task.title);
  const titleInputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (editingTitle && titleInputRef.current) {
      titleInputRef.current.focus();
      titleInputRef.current.select();
    }
  }, [editingTitle]);

  const splitCsv = (s: string) =>
    s.split(",").map((x) => x.trim()).filter(Boolean);

  // #316: URL field may contain markdown links `[Label](https://…)` which
  // themselves can contain commas (in brackets or URL query strings).
  // Split on top-level commas only — commas nested inside `[]` or `()`
  // are treated as literal content.
  const splitUrlCsv = (s: string): string[] => {
    const out: string[] = [];
    let buf = "";
    let bracket = 0;
    let paren = 0;
    for (const ch of s) {
      if (ch === "[") bracket++;
      else if (ch === "]") bracket = Math.max(0, bracket - 1);
      else if (ch === "(") paren++;
      else if (ch === ")") paren = Math.max(0, paren - 1);
      if (ch === "," && bracket === 0 && paren === 0) {
        const t = buf.trim();
        if (t) out.push(t);
        buf = "";
        continue;
      }
      buf += ch;
    }
    const tail = buf.trim();
    if (tail) out.push(tail);
    return out;
  };

  const invalidateTaskCaches = () => {
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["my-tasks"] });
    qc.invalidateQueries({ queryKey: ["agenda"] });
    qc.invalidateQueries({ queryKey: ["note"] });
    qc.invalidateQueries({ queryKey: ["features"] });
    qc.invalidateQueries({ queryKey: ["task"] });
  };

  const applyApiError = (e: unknown, fallbackContext: string) => {
    if (e instanceof ApiError) {
      if (e.status === 403) {
        if (/no access to project/i.test(e.detail)) {
          setErr("Permission denied: you don't have access to this task's project. Ask an admin to add you to the project, or to add you as an @owner of this task.");
        } else if (/manager role/i.test(e.detail)) {
          setErr("Permission denied: this action requires the project manager role.");
        } else if (/own/i.test(e.detail)) {
          setErr(`You can't ${fallbackContext} this task — only its @owners (or a project manager / admin) can.`);
        } else {
          setErr(`Permission denied: ${e.detail}`);
        }
      } else {
        setErr(`${e.status}: ${e.detail}`);
      }
    } else {
      const msg = (e as { message?: string })?.message ?? String(e);
      setErr(msg);
    }
  };

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
      // #314: link tokens.  Only send when the CSV actually differs from
      // the initial value, so unchanged links don't force a rewrite of
      // the markdown line every time the user hits Save.
      const linkFields: [string, string, string][] = [
        ["hsd",  hsd,      initialHsd],
        ["jira", jira,     initialJira],
        ["pr",   pr,       initialPr],
        ["url",  urlField, initialUrl],
      ];
      for (const [key, cur, orig] of linkFields) {
        if (cur !== orig) {
          patch[key] = key === "url" ? splitUrlCsv(cur) : splitCsv(cur);
        }
      }
      if (newNote.trim()) patch.add_note = newNote;
      if (Object.keys(patch).length === 0) return Promise.resolve(task);
      return api.updateTask(task.task_uuid ?? task.id, patch);
    },
    onSuccess: () => {
      invalidateTaskCaches();
      onClose();
    },
    onError: (e: unknown) => applyApiError(e, "edit"),
  });

  const patchTitle = useMutation({
    mutationFn: (newTitle: string) =>
      api.updateTask(task.task_uuid ?? task.id, { title: newTitle }),
    onSuccess: () => {
      setEditingTitle(false);
      invalidateTaskCaches();
    },
    onError: (e: unknown) => applyApiError(e, "edit"),
  });

  const del = useMutation({
    mutationFn: () => api.deleteTask(task.task_uuid ?? task.id),
    onSuccess: () => {
      invalidateTaskCaches();
      qc.invalidateQueries({ queryKey: ["tree"] });
      // When deleting an AR from within a swapped-into-AR view, bounce back
      // to the parent task instead of tearing the whole popover down.
      if (parentTask) {
        onBack();
      } else {
        onClose();
      }
    },
    onError: (e: unknown) => {
      applyApiError(e, "delete");
      setConfirmDelete(false);
    },
  });

  const cycleArStatus = useMutation({
    mutationFn: ({ id, status }: { id: number | string; status: string }) =>
      api.updateTask(id, { status }),
    onSuccess: () => {
      invalidateTaskCaches();
    },
    onError: (e: unknown) => applyApiError(e, "edit"),
  });

  const deleteAr = useMutation({
    mutationFn: (ref: number | string) => api.deleteTask(ref),
    onSuccess: () => {
      setConfirmDeleteArId(null);
      invalidateTaskCaches();
    },
    onError: (e: unknown) => {
      applyApiError(e, "delete");
      setConfirmDeleteArId(null);
    },
  });

  const addAr = useMutation({
    mutationFn: (title: string) =>
      api.addAr(task.task_uuid ?? task.id, { title }),
    onSuccess: () => {
      setNewArTitle("");
      invalidateTaskCaches();
    },
    onError: (e: unknown) => {
      applyApiError(e, "edit");
    },
  });

  const commitTitle = () => {
    const trimmed = titleDraft.trim();
    if (!trimmed) {
      setErr("Title cannot be blank.");
      return;
    }
    if (trimmed === task.title) {
      setEditingTitle(false);
      return;
    }
    setErr(null);
    patchTitle.mutate(trimmed);
  };

  const cancelTitleEdit = () => {
    setTitleDraft(task.title);
    setEditingTitle(false);
  };

  const arChildren = (task.children ?? []).filter((c) => c.kind === "ar");

  return (
    <>
      {parentTask && (
        <Breadcrumb parent={parentTask} onBack={onBack} onClose={onClose} />
      )}

      {/* Sticky header — always reachable even when body scrolls. */}
      <div className="p-5 pb-3 border-b border-slate-100 shrink-0
                      flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-xs text-slate-500 font-mono">
            T-{task.id} · {task.kind}
          </div>
          {editingTitle ? (
            <div className="flex items-center gap-1 mt-0.5">
              <input
                ref={titleInputRef}
                className="border rounded px-2 py-1 text-sm font-semibold flex-1 min-w-0"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    commitTitle();
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    e.stopPropagation();
                    cancelTitleEdit();
                  }
                }}
                disabled={patchTitle.isPending}
                aria-label="Edit title"
              />
              <button
                type="button"
                onClick={commitTitle}
                disabled={patchTitle.isPending}
                className="rounded bg-sky-600 text-white px-2 py-1 text-xs disabled:opacity-50"
                title="Save (Enter)"
                aria-label="Save title"
              >
                {patchTitle.isPending ? "…" : "✓"}
              </button>
              <button
                type="button"
                onClick={cancelTitleEdit}
                disabled={patchTitle.isPending}
                className="rounded border border-slate-300 px-2 py-1 text-xs"
                title="Cancel (Esc)"
                aria-label="Cancel title edit"
              >
                ✗
              </button>
            </div>
          ) : (
            <div className="flex items-start gap-1.5 mt-0.5">
              <h3 className="font-semibold text-base [overflow-wrap:anywhere] min-w-0 flex-1">
                <TitleWithBreakHints text={task.title} />
              </h3>
              <button
                type="button"
                onClick={() => { setTitleDraft(task.title); setEditingTitle(true); }}
                className="shrink-0 text-orange-500 hover:text-orange-700 text-sm leading-none py-0.5 px-1"
                title="Edit title"
                aria-label="Edit title"
              >
                ✎
              </button>
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-slate-400 hover:text-slate-700 text-lg leading-none"
          title="Close (Esc)"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      {/* Scrollable body — form fields live here. */}
      <div className="p-5 pt-3 overflow-y-auto flex-1">
        <form
          id="task-edit-form"
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

          {/* #314: external-URL capsule tokens. Each field is CSV; whitespace
              inside a value is rejected server-side. Values become clickable
              chips rendered by <LinkChips />. */}
          <Field label="HSD" hint="Comma-separated HSD IDs. e.g. 1234567, 2345678">
            <input className="border rounded px-2 py-1 text-sm w-full font-mono"
              value={hsd} onChange={(e) => setHsd(e.target.value)}
              placeholder="1234567" />
          </Field>
          <Field label="JIRA" hint="Comma-separated Jira keys. e.g. ABC-42, XYZ-9">
            <input className="border rounded px-2 py-1 text-sm w-full font-mono"
              value={jira} onChange={(e) => setJira(e.target.value)}
              placeholder="ABC-42" />
          </Field>
          <Field label="PR" hint="Comma-separated GitHub PRs as owner/repo#N.">
            <input className="border rounded px-2 py-1 text-sm w-full font-mono"
              value={pr} onChange={(e) => setPr(e.target.value)}
              placeholder="owner/repo#42" />
          </Field>
          <Field label="URLs" hint="Comma-separated. Preferred syntax: [Label](https://…) — the label becomes the chip text.">
            <input className="border rounded px-2 py-1 text-sm w-full font-mono"
              value={urlField} onChange={(e) => setUrlField(e.target.value)}
              placeholder="[Design Doc](https://example.com/design)" />
          </Field>

          {extraTagChips(task).length > 0 && (
            <Field label="Tags" hint="Bare `#tag` attributes parsed from the .md file. Add or remove by editing the source markdown.">
              <div className="flex flex-wrap gap-1">
                {extraTagChips(task).map((c) => (
                  <span
                    key={c.reactKey}
                    className="chip chip-tag"
                    title={c.value ? `${c.key} = ${c.value}` : `Tag: #${c.key}`}
                  >
                    #{c.key}
                    {c.value ? <span className="opacity-60">={c.value}</span> : null}
                  </span>
                ))}
              </div>
            </Field>
          )}

          {task.kind === "task" && (
            <Field
              label={`Action requests${arChildren.length ? ` (${arChildren.length})` : ""}`}
              hint="Click the pencil to edit an AR in this popover. Click the status chip to cycle it. Click the trash to delete."
            >
              {arChildren.length > 0 && (
                <ul className="border rounded divide-y bg-slate-50 mb-2 max-h-56 overflow-y-auto">
                  {arChildren.map((ar) => (
                    <ArRow
                      key={ar.id}
                      ar={ar}
                      confirmDelete={confirmDeleteArId === ar.id}
                      onCycleStatus={() => {
                        setErr(null);
                        cycleArStatus.mutate({
                          id: ar.task_uuid ?? ar.id,
                          status: nextArStatus(ar.status),
                        });
                      }}
                      onEdit={() => onSwapToAr(ar.task_uuid ?? ar.id)}
                      onRequestDelete={() => setConfirmDeleteArId(ar.id)}
                      onCancelDelete={() => setConfirmDeleteArId(null)}
                      onConfirmDelete={() => {
                        setErr(null);
                        deleteAr.mutate(ar.task_uuid ?? ar.id);
                      }}
                      deletePending={deleteAr.isPending && confirmDeleteArId === ar.id}
                    />
                  ))}
                </ul>
              )}
              {arChildren.length === 0 && (
                <div className="text-xs italic text-slate-400 border border-dashed rounded p-2 mb-2">
                  No action requests yet.
                </div>
              )}
              <div className="flex gap-2">
                <input
                  className="border rounded px-2 py-1 text-sm flex-1"
                  value={newArTitle}
                  onChange={(e) => setNewArTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && newArTitle.trim() && !addAr.isPending) {
                      e.preventDefault();
                      setErr(null);
                      addAr.mutate(newArTitle.trim());
                    }
                  }}
                  placeholder="Add an AR (e.g. follow up with @bob on perf)"
                />
                <button
                  type="button"
                  onClick={() => {
                    if (!newArTitle.trim() || addAr.isPending) return;
                    setErr(null);
                    addAr.mutate(newArTitle.trim());
                  }}
                  disabled={!newArTitle.trim() || addAr.isPending}
                  className="rounded bg-amber-600 text-white px-3 py-1 text-xs disabled:opacity-50"
                >
                  {addAr.isPending ? "adding…" : "+ AR"}
                </button>
              </div>
            </Field>
          )}

          <Field label="Notes — history" hint={noteHistory.length === 0 ? "No prior notes." : `${noteHistory.length} entr${noteHistory.length === 1 ? "y" : "ies"}, oldest first. Read-only — entries are append-only and preserved verbatim from the .md file.`}>
            {noteHistory.length === 0 ? (
              <div className="text-xs italic text-slate-400 border border-dashed rounded p-2">
                (none)
              </div>
            ) : (
              <ul className="border rounded divide-y bg-slate-50 max-h-32 overflow-y-auto">
                {noteHistory.map((line, i) => (
                  <li key={i} className="px-2 py-1 text-xs font-mono text-slate-700 whitespace-pre-wrap break-words">
                    {line}
                  </li>
                ))}
              </ul>
            )}
          </Field>

          <Field label="Add a note" hint={task.kind === "task"
            ? "Appended as a new `#note` continuation line. Auto-prefixed with timestamp + your @handle. For action items, use the 'Add an AR' field above instead — typing `!AR …` here will be rejected."
            : "Appended as a new `#note` continuation line. Auto-prefixed with timestamp + your @handle."}>
            <textarea
              className="border rounded px-2 py-1 text-sm w-full font-mono"
              rows={3}
              value={newNote}
              onChange={(e) => setNewNote(e.target.value)}
              placeholder="e.g. filed bug 12345; waiting on @alice for review"
            />
          </Field>

          {err && <div className="text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{err}</div>}
        </form>
      </div>

      {/* Sticky footer — Save/Cancel always visible regardless of body scroll. */}
      <div className="p-4 border-t border-slate-100 shrink-0
                      flex justify-between items-center gap-2 bg-white">
        {confirmDelete ? (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-rose-700">
              Delete this {task.kind === "ar" ? "AR" : "task and all its children"}?
            </span>
            <button type="button" onClick={() => setConfirmDelete(false)}
              className="rounded border px-2 py-0.5">no</button>
            <button type="button" onClick={() => { setErr(null); del.mutate(); }}
              disabled={del.isPending}
              className="rounded bg-rose-600 text-white px-2 py-0.5 disabled:opacity-50">
              {del.isPending ? "deleting…" : "yes, delete"}
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => setConfirmDelete(true)}
            className="text-xs text-rose-600 hover:text-rose-800 underline"
            title={task.kind === "ar"
              ? "Remove this AR line from the source .md file"
              : "Remove this task line (and any sub-tasks / ARs / #note continuations) from the source .md file"}>
            Delete {task.kind === "ar" ? "AR" : "task"}
          </button>
        )}
        <div className="flex gap-2">
          <button type="button" onClick={onClose}
            className="rounded border px-3 py-1 text-sm">cancel</button>
          <button type="submit" form="task-edit-form" disabled={save.isPending}
            className="rounded bg-sky-600 text-white px-3 py-1 text-sm disabled:opacity-50">
            {save.isPending ? "saving…" : "save"}
          </button>
        </div>
      </div>
    </>
  );
}

function Breadcrumb({ parent, onBack, onClose }: {
  parent: Task;
  onBack: () => void;
  onClose: () => void;
}) {
  return (
    <div className="px-4 py-1.5 border-b border-slate-100 shrink-0 bg-slate-50
                    flex items-center justify-between gap-2 text-xs">
      <button
        type="button"
        onClick={onBack}
        className="flex items-center gap-1 text-slate-600 hover:text-sky-700 min-w-0"
        title="Back to parent task"
      >
        <span aria-hidden>←</span>
        <span className="truncate max-w-[36ch]">
          Back to T-{parent.id}: {parent.title}
        </span>
      </button>
      <button
        type="button"
        onClick={onClose}
        className="text-slate-400 hover:text-slate-700 text-base leading-none px-1"
        title="Close (Esc)"
        aria-label="Close popover"
      >
        ×
      </button>
    </div>
  );
}

interface ArRowProps {
  ar: ChildTask;
  confirmDelete: boolean;
  onCycleStatus: () => void;
  onEdit: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  deletePending: boolean;
}

function ArRow({
  ar, confirmDelete, onCycleStatus, onEdit,
  onRequestDelete, onCancelDelete, onConfirmDelete, deletePending,
}: ArRowProps) {
  const style = AR_STATUS_STYLES[ar.status] ?? AR_STATUS_STYLES.default;
  return (
    <li className="px-2 py-1.5 flex items-center gap-2 text-xs">
      <button
        type="button"
        onClick={onCycleStatus}
        className={`shrink-0 rounded-full px-2 py-0.5 font-medium border ${style} hover:opacity-80`}
        title={`Status: ${ar.status} — click to cycle to next`}
        aria-label={`Cycle status for AR ${ar.title}`}
      >
        {ar.status}
      </button>
      <div className="flex-1 min-w-0 truncate [overflow-wrap:anywhere]" title={ar.title}>
        <TitleWithBreakHints text={ar.title} />
      </div>
      {ar.eta && (
        <span className="shrink-0 text-slate-500 font-mono" title={`ETA: ${ar.eta}`}>
          {ar.eta}
        </span>
      )}
      {confirmDelete ? (
        <span className="shrink-0 flex items-center gap-1">
          <button type="button" onClick={onCancelDelete}
            className="rounded border px-1.5 py-0.5">no</button>
          <button type="button" onClick={onConfirmDelete}
            disabled={deletePending}
            className="rounded bg-rose-600 text-white px-1.5 py-0.5 disabled:opacity-50">
            {deletePending ? "…" : "yes"}
          </button>
        </span>
      ) : (
        <>
          <button
            type="button"
            onClick={onEdit}
            className="shrink-0 text-orange-500 hover:text-orange-700 leading-none px-1"
            title="Edit this AR"
            aria-label={`Edit AR ${ar.title}`}
          >
            ✎
          </button>
          <button
            type="button"
            onClick={onRequestDelete}
            className="shrink-0 text-rose-600 hover:text-rose-800 leading-none px-1"
            title="Delete this AR"
            aria-label={`Delete AR ${ar.title}`}
          >
            <TrashIcon />
          </button>
        </>
      )}
    </li>
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

/**
 * Inline SVG trash icon.
 *
 * We can't use the 🗑 emoji here — every browser renders emoji through
 * a color-glyph font (Apple Color Emoji / Noto Color Emoji / Segoe UI
 * Emoji) that ignores the CSS `color` property, so the icon shows up
 * in its native muted grey regardless of the Tailwind class on its
 * parent. A stroked SVG that inherits `currentColor` picks up the
 * `text-rose-600 hover:text-rose-800` on the button cleanly.
 */
function TrashIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}
