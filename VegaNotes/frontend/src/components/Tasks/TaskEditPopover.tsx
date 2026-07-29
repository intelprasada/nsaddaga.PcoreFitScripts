import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type ChildTask, type Task } from "../../api/client";
import { TitleWithBreakHints } from "../../lib/titleWrap";
import { extraTagChips } from "../../lib/tagChips";
import { validateProgress, validateNoSpaceCsv } from "../../lib/taskFieldValidation";
import { nextArStatus, AR_STATUS_STYLES } from "./arStatus";
import {
  parseProgressValue, progressColor, PROGRESS_COLOR_CLASS,
  sparklinePoints, trendBetween, type ParsedProgress,
} from "../../lib/progressChip";

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
          className="bg-white rounded-lg shadow-xl w-[880px] max-w-[95vw]
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
  // #320: single-valued progress.
  const initialProgress = ((): string => {
    const v = task.attrs.progress;
    if (v == null) return "";
    return Array.isArray(v) ? (v[0] ?? "") : String(v);
  })();
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
  // #320
  const [progress, setProgress] = useState(initialProgress);
  const [newNote, setNewNote] = useState("");
  const [newArTitle, setNewArTitle] = useState("");
  // #333: per-note inline edit + delete-confirm state (index into note_history).
  const [editingNoteIdx, setEditingNoteIdx] = useState<number | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [confirmDeleteNoteIdx, setConfirmDeleteNoteIdx] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmDeleteArId, setConfirmDeleteArId] = useState<number | null>(null);
  // Links section starts expanded only when at least one link field already
  // has a value — the common (empty) case stays collapsed so four rarely-used
  // inputs don't dominate the form (#329).
  const [linksOpen, setLinksOpen] = useState<boolean>(
    () => !!(initialHsd || initialJira || initialPr || initialUrl),
  );

  // Live per-field validation mirroring the backend PATCH rules (#329). Save
  // is disabled while any of these is non-null; each surfaces inline under its
  // field so the user sees the problem before hitting a server 400.
  const fieldErrors = {
    progress: validateProgress(progress),
    hsd: validateNoSpaceCsv(hsd, "HSD"),
    jira: validateNoSpaceCsv(jira, "JIRA"),
    pr: validateNoSpaceCsv(pr, "PR"),
  };
  const hasFieldErrors = Object.values(fieldErrors).some(Boolean);

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
      // #320: single-valued progress token. Trim before diffing so a
      // stray whitespace edit doesn't force a rewrite.
      if (progress.trim() !== initialProgress.trim()) {
        patch.progress = progress.trim();
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

  // #333: edit / delete individual notes, like ARs. `expect` carries the
  // note's current text so a stale index can't rewrite the wrong entry.
  const editNote = useMutation({
    mutationFn: ({ index, text, expect }: { index: number; text: string; expect: string }) =>
      api.updateTask(task.task_uuid ?? task.id, { edit_note: { index, text, expect } }),
    onSuccess: () => {
      setEditingNoteIdx(null);
      setNoteDraft("");
      invalidateTaskCaches();
    },
    onError: (e: unknown) => applyApiError(e, "edit"),
  });

  const deleteNote = useMutation({
    mutationFn: ({ index, expect }: { index: number; expect: string }) =>
      api.updateTask(task.task_uuid ?? task.id, { delete_note: { index, expect } }),
    onSuccess: () => {
      setConfirmDeleteNoteIdx(null);
      invalidateTaskCaches();
    },
    onError: (e: unknown) => {
      applyApiError(e, "delete");
      setConfirmDeleteNoteIdx(null);
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
                className="vega-input text-sm font-semibold flex-1 min-w-0"
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
          className="grid grid-cols-1 md:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-5"
          onSubmit={(e) => { e.preventDefault(); if (hasFieldErrors) return; setErr(null); save.mutate(); }}
        >
          {/* METADATA pane (right / order-2) — status, people, links and tags
              are set occasionally, so they take the narrower column (#329). */}
          <div className="space-y-4 min-w-0 md:order-2">
          {/* Status & schedule — short fields laid out two-up (#329). */}
          <Section title="Status & schedule">
            <FieldGrid>
              <Field label="Status">
                <select className="vega-select"
                  value={status} onChange={(e) => setStatus(e.target.value)}>
                  {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label="Priority">
                <select className="vega-select"
                  value={priority} onChange={(e) => setPriority(e.target.value)}>
                  {PRIORITIES.map((p) => <option key={p} value={p}>{p || "(none)"}</option>)}
                </select>
              </Field>
            </FieldGrid>
            <FieldGrid>
              <Field label="ETA" hint="Intel WW (2026-W18) or ISO date. Empty to clear.">
                <input className="vega-input font-mono"
                  value={eta} onChange={(e) => setEta(e.target.value)}
                  placeholder="2026-W18" />
              </Field>
              <Field label="Progress" hint="`N`, `N/D`, or `N/D label`. Empty clears." error={fieldErrors.progress}>
                <input className={`vega-input font-mono ${fieldErrors.progress ? "border-rose-400 focus:ring-rose-100" : ""}`}
                  aria-invalid={!!fieldErrors.progress}
                  value={progress} onChange={(e) => setProgress(e.target.value)}
                  placeholder="30/54 fixed" />
              </Field>
            </FieldGrid>
            <ProgressHistorySection task={task} />
          </Section>

          {/* People & scope. */}
          <Section title="People &amp; scope">
            <Field label="Owners" hint="Comma-separated. Type to autocomplete known users.">
              <input className="vega-input"
                value={owners} onChange={(e) => setOwners(e.target.value)}
                placeholder="alice, bob" list="known-users" />
              <datalist id="known-users">
                {knownUsers.map((u) => <option key={u} value={u} />)}
              </datalist>
            </Field>
            <Field label="Features" hint="Comma-separated.">
              <input className="vega-input"
                value={features} onChange={(e) => setFeatures(e.target.value)}
                placeholder="auth, billing" />
            </Field>
          </Section>

          {/* External links — collapsed by default when empty so the four
              rarely-used fields don't dominate the form (#314/#329). */}
          <section className="vega-section space-y-3">
            <button type="button"
              className="vega-section-title hover:text-slate-700"
              onClick={() => setLinksOpen((o) => !o)}
              aria-expanded={linksOpen}>
              <span className="text-slate-400">{linksOpen ? "▾" : "▸"}</span>
              Links
              {(() => {
                const n = [hsd, jira, pr, urlField].filter((s) => s.trim()).length;
                return !linksOpen && n > 0
                  ? <span className="ml-1 rounded-full bg-slate-200 px-1.5 text-[10px] text-slate-600 normal-case">{n}</span>
                  : null;
              })()}
            </button>
            {linksOpen && (
              <>
                <FieldGrid>
                  <Field label="HSD" hint="Comma-separated IDs." error={fieldErrors.hsd}>
                    <input className={`vega-input font-mono ${fieldErrors.hsd ? "border-rose-400 focus:ring-rose-100" : ""}`}
                      aria-invalid={!!fieldErrors.hsd}
                      value={hsd} onChange={(e) => setHsd(e.target.value)}
                      placeholder="1234567" />
                  </Field>
                  <Field label="JIRA" hint="Comma-separated keys." error={fieldErrors.jira}>
                    <input className={`vega-input font-mono ${fieldErrors.jira ? "border-rose-400 focus:ring-rose-100" : ""}`}
                      aria-invalid={!!fieldErrors.jira}
                      value={jira} onChange={(e) => setJira(e.target.value)}
                      placeholder="ABC-42" />
                  </Field>
                </FieldGrid>
                <FieldGrid>
                  <Field label="PR" hint="owner/repo#N, comma-separated." error={fieldErrors.pr}>
                    <input className={`vega-input font-mono ${fieldErrors.pr ? "border-rose-400 focus:ring-rose-100" : ""}`}
                      aria-invalid={!!fieldErrors.pr}
                      value={pr} onChange={(e) => setPr(e.target.value)}
                      placeholder="owner/repo#42" />
                  </Field>
                  <Field label="URLs" hint="[Label](https://…), comma-separated.">
                    <input className="vega-input font-mono"
                      value={urlField} onChange={(e) => setUrlField(e.target.value)}
                      placeholder="[Design Doc](https://…)" />
                  </Field>
                </FieldGrid>
              </>
            )}
          </section>

          {extraTagChips(task).length > 0 && (
            <Section title="Tags">
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
              <div className="vega-field-hint">Bare <code>#tag</code> attributes from the .md file — edit the source to change.</div>
            </Section>
          )}
          </div>

          {/* PRIMARY pane (left / order-1) — ARs and notes are added/removed far
              more often than metadata, so they lead and take the wider column. */}
          <div className="space-y-4 min-w-0 md:order-1">
          {task.kind === "task" && (
            <Section title={`Action requests${arChildren.length ? ` (${arChildren.length})` : ""}`}>
              <div className="vega-field-hint -mt-1">Pencil edits · status chip cycles · trash deletes.</div>
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
                  className="vega-input flex-1"
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
            </Section>
          )}

          <Section title="Notes">
            <Field label={`History${noteHistory.length ? ` (${noteHistory.length})` : ""}`} hint={noteHistory.length === 0 ? "No prior notes." : "Oldest first · hover a note to edit or delete."}>
              {noteHistory.length === 0 ? (
                <div className="text-xs italic text-slate-400 border border-dashed rounded p-2">
                  (none)
                </div>
              ) : (
                <ul className="border rounded divide-y bg-white max-h-40 overflow-y-auto">
                  {noteHistory.map((line, i) => (
                    <NoteRow
                      key={i}
                      text={line}
                      editing={editingNoteIdx === i}
                      draft={noteDraft}
                      confirmDelete={confirmDeleteNoteIdx === i}
                      busy={(editNote.isPending && editingNoteIdx === i) || (deleteNote.isPending && confirmDeleteNoteIdx === i)}
                      onStartEdit={() => { setErr(null); setConfirmDeleteNoteIdx(null); setEditingNoteIdx(i); setNoteDraft(line); }}
                      onDraftChange={setNoteDraft}
                      onCancelEdit={() => { setEditingNoteIdx(null); setNoteDraft(""); }}
                      onSaveEdit={() => {
                        const t = noteDraft.trim();
                        if (!t || t === line) { setEditingNoteIdx(null); setNoteDraft(""); return; }
                        setErr(null);
                        editNote.mutate({ index: i, text: t, expect: line });
                      }}
                      onRequestDelete={() => { setErr(null); setEditingNoteIdx(null); setConfirmDeleteNoteIdx(i); }}
                      onCancelDelete={() => setConfirmDeleteNoteIdx(null)}
                      onConfirmDelete={() => { setErr(null); deleteNote.mutate({ index: i, expect: line }); }}
                    />
                  ))}
                </ul>
              )}
            </Field>

            <Field label="Add a note" hint={task.kind === "task"
              ? "Appended as a `#note` line, prefixed with timestamp + your @handle. Use 'Add an AR' above for action items."
              : "Appended as a `#note` line, prefixed with timestamp + your @handle."}>
              <textarea
                className="vega-textarea font-mono"
                rows={3}
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                placeholder="e.g. filed bug 12345; waiting on @alice for review"
              />
            </Field>
          </Section>
          </div>

          {err && <div className="md:col-span-2 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{err}</div>}
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
          // Destructive action kept visually distinct (outlined danger) and far
          // from the primary Save on the opposite edge, behind a confirm step.
          <button type="button" onClick={() => setConfirmDelete(true)}
            className="inline-flex items-center gap-1.5 rounded-md border border-rose-200 px-2.5 py-1 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50 hover:border-rose-300"
            title={task.kind === "ar"
              ? "Remove this AR line from the source .md file"
              : "Remove this task line (and any sub-tasks / ARs / #note continuations) from the source .md file"}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 6h18" /><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" /><path d="M6 6l1 14a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-14" />
            </svg>
            Delete {task.kind === "ar" ? "AR" : "task"}
          </button>
        )}
        <div className="flex items-center gap-2">
          {hasFieldErrors && (
            <span className="text-[11px] text-rose-500">Fix highlighted fields</span>
          )}
          <button type="button" onClick={onClose}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100">cancel</button>
          <button type="submit" form="task-edit-form" disabled={save.isPending || hasFieldErrors}
            title={hasFieldErrors ? "Resolve the highlighted validation errors first" : undefined}
            className="rounded-md bg-sky-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:opacity-50 disabled:hover:bg-sky-600">
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

// #333: an individual note row — mirrors ArRow's view / inline-edit / confirm-
// delete affordances so notes feel first-class like action requests.
interface NoteRowProps {
  text: string;
  editing: boolean;
  draft: string;
  confirmDelete: boolean;
  busy: boolean;
  onStartEdit: () => void;
  onDraftChange: (v: string) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}

function NoteRow({
  text, editing, draft, confirmDelete, busy,
  onStartEdit, onDraftChange, onCancelEdit, onSaveEdit,
  onRequestDelete, onCancelDelete, onConfirmDelete,
}: NoteRowProps) {
  if (editing) {
    return (
      <li className="px-2 py-1.5 flex items-start gap-2 text-xs bg-sky-50/50">
        <textarea
          autoFocus
          className="vega-textarea font-mono flex-1 min-h-[2.2rem]"
          rows={2}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSaveEdit(); }
            if (e.key === "Escape") { e.preventDefault(); onCancelEdit(); }
          }}
        />
        <span className="shrink-0 flex flex-col gap-1 pt-0.5">
          <button type="button" onClick={onSaveEdit} disabled={busy}
            className="rounded bg-sky-600 text-white px-2 py-0.5 disabled:opacity-50" title="Save (⌘/Ctrl+Enter)">
            {busy ? "…" : "save"}
          </button>
          <button type="button" onClick={onCancelEdit}
            className="rounded border px-2 py-0.5" title="Cancel (Esc)">cancel</button>
        </span>
      </li>
    );
  }
  return (
    <li className="group px-2 py-1.5 flex items-start gap-2 text-xs">
      <div className="flex-1 min-w-0 font-mono text-slate-700 whitespace-pre-wrap break-words">{text}</div>
      {confirmDelete ? (
        <span className="shrink-0 flex items-center gap-1">
          <button type="button" onClick={onCancelDelete} className="rounded border px-1.5 py-0.5">no</button>
          <button type="button" onClick={onConfirmDelete} disabled={busy}
            className="rounded bg-rose-600 text-white px-1.5 py-0.5 disabled:opacity-50">
            {busy ? "…" : "yes"}
          </button>
        </span>
      ) : (
        <span className="shrink-0 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          <button type="button" onClick={onStartEdit}
            className="text-orange-500 hover:text-orange-700 leading-none px-1" title="Edit this note" aria-label="Edit note">
            ✎
          </button>
          <button type="button" onClick={onRequestDelete}
            className="text-rose-600 hover:text-rose-800 leading-none px-1" title="Delete this note" aria-label="Delete note">
            <TrashIcon />
          </button>
        </span>
      )}
    </li>
  );
}

function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string | null; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="vega-field-label">{label}</div>
      {children}
      {error
        ? <div className="mt-1 flex items-start gap-1 text-[11px] text-rose-600">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mt-px shrink-0" aria-hidden="true">
              <circle cx="12" cy="12" r="10" /><path d="M12 8v4" /><path d="M12 16h.01" />
            </svg>
            <span>{error}</span>
          </div>
        : hint ? <div className="vega-field-hint">{hint}</div> : null}
    </label>
  );
}

// Grouping primitives for the popover body (#329). A Section is a titled,
// lightly-boxed cluster of related fields; FieldGrid lays short fields out
// two-up to use the modal's width instead of an ever-growing single column.
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="vega-section space-y-3">
      <div className="vega-section-title">{title}</div>
      {children}
    </section>
  );
}

function FieldGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
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

/**
 * #320: weekly history panel for a task's `#progress` metric.
 *
 * Renders a sparkline of the last-N percent readings plus a compact
 * table (week → N/D → label → arrow) driven by
 * `GET /api/tasks/{ref}/progress-history`.  Hidden entirely when the
 * task has no history (avoids empty-state clutter on tasks that don't
 * use the metric).
 */
function ProgressHistorySection({ task }: { task: Task }) {
  const ref = task.task_uuid ?? task.id;
  const { data, isLoading, isError } = useQuery({
    queryKey: ["task-progress-history", ref],
    queryFn: () => api.taskProgressHistory(ref),
    staleTime: 30_000,
  });

  if (isLoading) return null;
  if (isError) return null;
  const rows = data ?? [];
  if (rows.length === 0) return null;

  const parsed = rows.map((r) => ({
    week: r.week,
    p: parseProgressValue(
      r.denominator != null
        ? `${r.numerator}/${r.denominator}${r.label ? ` ${r.label}` : ""}`
        : `${r.numerator}${r.label ? ` ${r.label}` : ""}`,
    ),
    raw: r,
  })).filter((x) => x.p !== null) as { week: string; p: ParsedProgress; raw: typeof rows[number] }[];

  if (parsed.length === 0) return null;

  const values = parsed.map((x) => x.p.percent ?? x.p.numerator);
  const sparkW = 160;
  const sparkH = 32;
  const points = sparklinePoints(values, sparkW, sparkH);
  const latest = parsed[parsed.length - 1].p;
  const latestColor = progressColor(latest);

  return (
    <div className="mt-2 border rounded p-2 bg-slate-50">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs font-semibold text-slate-700">
          Weekly history <span className="opacity-60">({parsed.length})</span>
        </div>
        <svg
          width={sparkW}
          height={sparkH}
          viewBox={`0 0 ${sparkW} ${sparkH}`}
          className={PROGRESS_COLOR_CLASS[latestColor].split(" ").find((c) => c.startsWith("text-")) ?? "text-slate-700"}
          aria-hidden="true"
        >
          <polyline
            points={points}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        </svg>
      </div>
      <table className="w-full text-xs font-mono">
        <thead className="text-slate-500">
          <tr>
            <th className="text-left font-normal">Week</th>
            <th className="text-right font-normal">Reading</th>
            <th className="text-right font-normal">%</th>
            <th className="text-left font-normal pl-2">Label</th>
            <th className="text-right font-normal w-6"></th>
          </tr>
        </thead>
        <tbody>
          {parsed.map((row, i) => {
            const prev = i > 0 ? parsed[i - 1].p : null;
            const trend = trendBetween(prev, row.p);
            const arrow = trend === "up" ? "▲" : trend === "down" ? "▼" : "·";
            const arrowClass =
              trend === "up" ? "text-emerald-600" :
              trend === "down" ? "text-rose-600" : "text-slate-400";
            const numeric = row.p.denominator != null
              ? `${row.p.numerator}/${row.p.denominator}`
              : String(row.p.numerator);
            return (
              <tr key={row.week} className="border-t border-slate-200">
                <td className="text-slate-700">{row.week}</td>
                <td className="text-right">{numeric}</td>
                <td className="text-right text-slate-500">
                  {row.p.percent != null ? `${row.p.percent}%` : "—"}
                </td>
                <td className="pl-2 text-slate-500">{row.p.label ?? ""}</td>
                <td className={"text-right " + arrowClass}>{arrow}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
