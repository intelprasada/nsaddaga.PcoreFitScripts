import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ChildTask, type Task } from "../../api/client";

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

export function TaskCard({ task, onOpen }: Props) {
  const prio = (task.attrs.priority as string) ?? "";
  const accent = PRIO_COLOR[prio] ?? "border-l-slate-300";
  const ars = (task.children ?? []).filter((c) => c.kind === "ar");
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();

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
        <div className="font-medium text-sm">{task.title}</div>
        {task.eta && <span className="chip chip-eta">{task.eta}</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {prio && <span className="chip chip-priority">{prio}</span>}
        {task.owners.map((o) => <span key={o} className="chip chip-owner">@{o}</span>)}
        {task.projects.map((p) => <span key={p} className="chip chip-project">#{p}</span>)}
        {task.features.map((f) => <span key={f} className="chip chip-feature">★{f}</span>)}
        {task.status !== "todo" && <span className="chip chip-status">{task.status}</span>}
      </div>

      {ars.length > 0 && (
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded((x) => !x); }}
          className="mt-2 w-full text-left text-[11px] text-amber-700 hover:text-amber-900 flex items-center gap-1"
          title="Action Required items (subtasks declared with !AR)"
        >
          <span>{expanded ? "▾" : "▸"}</span>
          <span className="font-semibold">{ars.length} AR{ars.length === 1 ? "" : "s"}</span>
          <span className="text-slate-500">({arDone} done / {ars.length - arDone} open)</span>
        </button>
      )}
      <AnimatePresence initial={false}>
        {expanded && ars.length > 0 && (
          <motion.ul
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="mt-1 ml-3 border-l border-amber-200 pl-2 space-y-1 overflow-hidden"
          >
            {ars.map((a) => (
              <ArRow key={a.id} ar={a} onCycle={() =>
                cycleAr.mutate({ id: a.id, status: AR_NEXT[a.status] ?? "in-progress" })
              } />
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function ArRow({ ar, onCycle }: { ar: ChildTask; onCycle: () => void }) {
  const done = ar.status === "done";
  return (
    <li className="flex items-center gap-2 text-[12px]">
      <button
        onClick={(e) => { e.stopPropagation(); onCycle(); }}
        className={`chip text-[10px] cursor-pointer ${
          done
            ? "chip-status"
            : ar.status === "in-progress"
              ? "bg-sky-100 text-sky-900"
              : ar.status === "blocked"
                ? "bg-rose-100 text-rose-900"
                : "bg-slate-200 text-slate-700"
        }`}
        title="Click to cycle status (todo → in-progress → done → todo)"
      >
        {ar.status}
      </button>
      <span className={done ? "line-through text-slate-400" : "text-slate-700"}>
        {ar.title}
      </span>
      {ar.eta && <span className="chip chip-eta text-[10px]">{ar.eta}</span>}
    </li>
  );
}
