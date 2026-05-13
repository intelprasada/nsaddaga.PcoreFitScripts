/**
 * QuickChips — inline interactive chips for status, priority, ETA, and owners.
 *
 * Each chip fires a single PATCH /api/tasks/{id} when the user commits a
 * change.  Optimistic updates keep the UI snappy; the server's authoritative
 * value lands when react-query's ["tasks"] invalidation resolves.
 *
 * Priority cycling is debounced (600 ms) so rapid clicks don't fire a PATCH
 * for every intermediate value.
 *
 * Pass canWrite={false} to render chips as static read-only badges.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { formatIntelWw } from "@veganotes/parser";

// ── helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  "todo":        "bg-slate-100 text-slate-700",
  "in-progress": "bg-yellow-100 text-yellow-800",
  "blocked":     "bg-rose-100 text-rose-800",
  "done":        "bg-emerald-100 text-emerald-800",
};

const PRIO_COLORS: Record<string, string> = {
  P0: "bg-rose-100 text-rose-700",
  P1: "bg-orange-100 text-orange-700",
  P2: "bg-amber-100 text-amber-700",
  P3: "bg-emerald-100 text-emerald-700",
};

const PRIO_CYCLE = ["", "P0", "P1", "P2", "P3"] as const;
const STATUSES   = ["todo", "in-progress", "blocked", "done"] as const;

function etaLabel(eta: string): string {
  if (/^\d{4}-\d{2}-\d{2}/.test(eta)) {
    try { return formatIntelWw(eta.slice(0, 10)); } catch { /* ignore */ }
  }
  return eta;
}

// ── shared hook ──────────────────────────────────────────────────────────────

function useTaskPatch(taskId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.updateTask(taskId, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["agenda"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
  });
}

// ── StatusChip ────────────────────────────────────────────────────────────────

export function StatusChip({ task, canWrite }: { task: Task; canWrite: boolean }) {
  const [status, setStatus] = useState(task.status);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const { mutate, isPending } = useTaskPatch(task.id);

  // Sync with authoritative server data after query refetch.
  useEffect(() => { setStatus(task.status); }, [task.status]);

  // Close dropdown on outside click. The popover is portaled, so the
  // hit-test must include both the chip wrapper AND the popover node.
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t)) return;
      if (popRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  // Anchor the portaled popover to the chip button on open / scroll / resize.
  // Portaling escapes ancestor `overflow-hidden` (issue #199 — the My-Tasks
  // group card was clipping the dropdown).
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 4, left: r.left });
    };
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [open]);

  const pick = (s: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setStatus(s);
    setOpen(false);
    mutate({ status: s });
  };

  const chipColor = STATUS_COLORS[status] ?? "bg-slate-100 text-slate-600";

  if (!canWrite) {
    return (
      <span className={`chip ${chipColor}`}>{status}</span>
    );
  }

  return (
    <div ref={ref} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        ref={btnRef}
        onClick={() => setOpen((o) => !o)}
        className={`chip ${chipColor} cursor-pointer hover:opacity-80 transition-opacity`}
        title="Click to change status"
        disabled={isPending}
      >
        {isPending ? <span className="animate-pulse">…</span> : status}
      </button>
      {open && pos && createPortal(
        <div
          ref={popRef}
          style={{ position: "fixed", top: pos.top, left: pos.left, zIndex: 50 }}
          className="bg-white border border-slate-200 rounded-lg shadow-lg min-w-[130px] py-1 overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          {STATUSES.map((s) => (
            <button
              key={s}
              onClick={(e) => pick(s, e)}
              className={`flex w-full items-center px-2 py-1 text-xs hover:bg-slate-50 transition-colors gap-1.5
                ${s === status ? "bg-slate-50 font-semibold" : ""}`}
            >
              <span className={`chip ${STATUS_COLORS[s]} pointer-events-none`}>{s}</span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}

// ── PriorityChip ──────────────────────────────────────────────────────────────

export function PriorityChip({ task, canWrite }: { task: Task; canWrite: boolean }) {
  const rawPrio = (task.attrs.priority as string) ?? "";
  const [prio, setPrio] = useState(rawPrio);
  const { mutate, isPending } = useTaskPatch(task.id);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setPrio(rawPrio); }, [rawPrio]);

  const cycle = (e: React.MouseEvent) => {
    e.stopPropagation();
    const idx = PRIO_CYCLE.indexOf(prio as typeof PRIO_CYCLE[number]);
    const next = PRIO_CYCLE[(idx + 1) % PRIO_CYCLE.length];
    setPrio(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => mutate({ priority: next }), 600);
  };

  const color = PRIO_COLORS[prio] ?? "bg-slate-100 text-slate-400";
  const label = prio || (canWrite ? "—" : "");

  if (!label) return null;

  return (
    <button
      onClick={canWrite ? cycle : (e) => e.stopPropagation()}
      className={`chip ${color} ${canWrite ? "cursor-pointer hover:opacity-80 transition-opacity" : "cursor-default"}`}
      title={canWrite ? `Priority: ${prio || "none"} — click to cycle` : `Priority: ${prio}`}
      disabled={isPending}
    >
      {isPending ? <span className="animate-pulse">…</span> : label}
    </button>
  );
}

// ── EtaChip ───────────────────────────────────────────────────────────────────

export function EtaChip({ task, canWrite }: { task: Task; canWrite: boolean }) {
  const [val, setVal]       = useState(task.eta ?? "");
  const [editing, setEditing] = useState(false);
  const { mutate, isPending } = useTaskPatch(task.id);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setVal(task.eta ?? ""); }, [task.eta]);

  const commit = (e?: React.SyntheticEvent) => {
    e?.stopPropagation();
    setEditing(false);
    const newEta = val.trim();
    if (newEta !== (task.eta ?? "")) mutate({ eta: newEta });
  };

  const label = val ? etaLabel(val) : (canWrite ? "ETA?" : "");
  if (!label && !canWrite) return null;

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="chip chip-eta font-mono w-24 border border-rose-300 outline-none bg-white px-1.5 py-0.5 rounded-full text-xs"
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") { e.preventDefault(); commit(e); }
          if (e.key === "Escape") { setEditing(false); setVal(task.eta ?? ""); }
        }}
        onClick={(e) => e.stopPropagation()}
        placeholder="2026-W18"
        autoFocus
      />
    );
  }

  return (
    <button
      onClick={(e) => { e.stopPropagation(); if (canWrite) setEditing(true); }}
      className={`chip chip-eta ${canWrite ? "cursor-pointer hover:opacity-80" : "cursor-default"} transition-opacity`}
      title={canWrite
        ? `ETA: ${task.eta || "not set"} — click to edit (ISO date or Intel WW)`
        : `ETA: ${task.eta}`}
      disabled={isPending}
    >
      {isPending ? <span className="animate-pulse">…</span> : label}
    </button>
  );
}

// ── OwnersChips ───────────────────────────────────────────────────────────────

export function OwnersChips({ task, canWrite }: { task: Task; canWrite: boolean }) {
  const [owners, setOwners]   = useState(task.owners);
  const [adding, setAdding]   = useState(false);
  const [newOwner, setNewOwner] = useState("");
  const { mutate, isPending }  = useTaskPatch(task.id);
  const { data: knownUsers = [] } = useQuery({ queryKey: ["users"], queryFn: () => api.users() });

  useEffect(() => { setOwners(task.owners); }, [task.owners]);

  const remove = (name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const next = owners.filter((o) => o !== name);
    setOwners(next);
    mutate({ owners: next });
  };

  const commitAdd = (nameOverride?: string) => {
    const n = (nameOverride ?? newOwner).trim();
    setAdding(false);
    setNewOwner("");
    if (!n || owners.includes(n)) return;
    const next = [...owners, n];
    setOwners(next);
    mutate({ owners: next });
  };

  return (
    <>
      {owners.map((o) => (
        <span key={o} className="chip chip-owner group/owner">
          @{o}
          {canWrite && (
            <button
              onClick={(e) => remove(o, e)}
              className="ml-0.5 opacity-0 group-hover/owner:opacity-100 transition-opacity leading-none text-sky-600 hover:text-rose-600"
              title={`Remove @${o}`}
            >
              ×
            </button>
          )}
        </span>
      ))}
      {canWrite && (
        adding ? (
          <>
            <input
              className="chip chip-owner font-mono w-20 border border-sky-300 outline-none bg-white px-1.5 py-0.5 rounded-full text-xs"
              value={newOwner}
              autoFocus
              onChange={(e) => setNewOwner(e.target.value)}
              onBlur={() => commitAdd()}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") { e.preventDefault(); commitAdd(); }
                if (e.key === "Escape") { setAdding(false); setNewOwner(""); }
              }}
              onClick={(e) => e.stopPropagation()}
              placeholder="user"
              list="quick-chip-users"
            />
            <datalist id="quick-chip-users">
              {knownUsers
                .filter((u) => !owners.includes(u))
                .map((u) => <option key={u} value={u} />)}
            </datalist>
          </>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); setAdding(true); }}
            className={`chip bg-sky-50 text-sky-400 hover:text-sky-700 hover:bg-sky-100
              border border-dashed border-sky-300 cursor-pointer transition-colors
              ${isPending ? "opacity-50 pointer-events-none" : ""}`}
            title="Add owner"
          >
            +
          </button>
        )
      )}
    </>
  );
}

// ── Public export ─────────────────────────────────────────────────────────────

export interface QuickChipsProps {
  task: Task;
  /** If false, all chips render as static read-only badges. Default: true. */
  canWrite?: boolean;
}

/**
 * Renders the four interactive quick-action chips for a task:
 *   status · priority · ETA · owners
 *
 * Drop this inside any task card or row that wants inline editing.
 */
export function QuickChips({ task, canWrite = true }: QuickChipsProps) {
  return (
    <>
      <StatusChip   task={task} canWrite={canWrite} />
      <PriorityChip task={task} canWrite={canWrite} />
      <EtaChip      task={task} canWrite={canWrite} />
      <OwnersChips  task={task} canWrite={canWrite} />
    </>
  );
}
