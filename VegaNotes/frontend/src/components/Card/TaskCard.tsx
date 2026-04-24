import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ChildTask, type Task } from "../../api/client";
import { formatIntelWw } from "@veganotes/parser";
import { useFontScale, FONT_SCALE_MAP } from "../../store/fontScale";

interface Props { task: Task; onOpen?: (t: Task) => void; }

const PRIO_COLOR: Record<string, string> = {
  P0: "border-l-rose-500", P1: "border-l-orange-500",
  P2: "border-l-amber-500", P3: "border-l-emerald-500",
};

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

function UuidChip({ uuid }: { uuid: string }) {
  const [copied, setCopied] = useState(false);
  const copy = (e: React.MouseEvent) => {
    e.stopPropagation();
    const write = navigator.clipboard?.writeText(uuid);
    const finish = () => { setCopied(true); setTimeout(() => setCopied(false), 1500); };
    if (write) write.then(finish).catch(finish); else finish();
  };
  return (
    <button
      onClick={copy}
      title={copied ? "Copied!" : "Click to copy task ID"}
      className="chip font-mono text-[10px] bg-slate-50 border border-slate-200 text-slate-400 hover:text-slate-600 hover:border-slate-400 transition-colors px-1.5 py-0.5"
    >
      {copied ? <span className="text-emerald-500 not-italic">✓</span> : uuid}
    </button>
  );
}

export function TaskCard({ task, onOpen }: Props) {
  const prio = (task.attrs.priority as string) ?? "";
  const accent = PRIO_COLOR[prio] ?? "border-l-slate-300";
  const ars = (task.children ?? []).filter((c) => c.kind === "ar");
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();
  const { scale } = useFontScale();
  const fs = FONT_SCALE_MAP[scale];

  const cycleAr = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api.updateTask(id, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["agenda"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
  });

  const arDone = ars.filter((a) => a.status === "done").length;

  return (
    <motion.div layout
      onClick={() => onOpen?.(task)}
      className={`card border-l-4 ${accent} cursor-pointer`}>
      <div className="flex items-start justify-between gap-2">
        <div className={`font-medium ${fs.title}`}>{task.title}</div>
        {task.eta && <span className="chip chip-eta" title={task.eta}>{etaLabel(task.eta)}</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {task.task_uuid && <UuidChip uuid={task.task_uuid} />}
        {prio && <span className="chip chip-priority">{prio}</span>}
        {task.owners.map((o) => <span key={o} className="chip chip-owner">@{o}</span>)}
        {task.projects.map((p) => <span key={p} className="chip chip-project">#{p}</span>)}
        {task.features.map((f) => <span key={f} className="chip chip-feature">★{f}</span>)}
        {task.status !== "todo" && <span className="chip chip-status">{task.status}</span>}
      </div>

      {ars.length > 0 && (
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
          className={`mt-2 w-full text-left ${fs.ar} text-amber-800 hover:text-amber-900 flex items-center gap-1.5 bg-amber-50 border border-amber-200 rounded px-2 py-1`}
          title="Action Required items (subtasks declared with !AR)"
        >
          <span className={fs.ar}>{expanded ? "▾" : "▸"}</span>
          <span className="font-bold">{ars.length} AR{ars.length === 1 ? "" : "s"}</span>
          <span className="text-slate-600">({arDone} done / {ars.length - arDone} open)</span>
        </button>
      )}
      <AnimatePresence initial={false}>
        {expanded && ars.length > 0 && (
          <motion.ul
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mt-1.5 ml-2 border-l-2 border-amber-300 pl-3 space-y-1.5 overflow-hidden"
          >
            {ars.map((a) => (
              <ArRow key={a.id} ar={a} arClass={fs.ar} bubbleClass={fs.bubble} onCycle={() =>
                cycleAr.mutate({ id: a.id, status: AR_NEXT[a.status] ?? "in-progress" })
              } />
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function ArRow({ ar, arClass, bubbleClass, onCycle }: {
  ar: ChildTask; arClass: string; bubbleClass: string; onCycle: () => void;
}) {
  const done = ar.status === "done";
  const statusLabel = ar.status === "in-progress" ? "wip" : ar.status;

  const bubbleColor =
    ar.status === "done"        ? "bg-emerald-100 text-emerald-800" :
    ar.status === "in-progress" ? "bg-yellow-100  text-yellow-800"  :
    ar.status === "blocked"     ? "bg-rose-100    text-rose-800"    :
                                  "bg-slate-100   text-slate-600";

  return (
    <li className={`flex items-center gap-2 ${arClass}`}>
      <button
        onClick={(e) => { e.stopPropagation(); onCycle(); }}
        title={`Status: ${ar.status} — click to cycle`}
        className={`chip ${bubbleClass} px-2 py-0.5 cursor-pointer font-medium ${bubbleColor}`}
      >
        {statusLabel}
      </button>
      <span className={done ? "line-through text-slate-400 font-medium" : "text-slate-700 font-medium"}>
        {ar.title}
      </span>
      {ar.eta && <span className={`chip chip-eta ${bubbleClass}`} title={ar.eta}>{etaLabel(ar.eta)}</span>}
    </li>
  );
}
