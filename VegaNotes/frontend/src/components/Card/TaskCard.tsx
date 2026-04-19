import { motion } from "framer-motion";
import type { Task } from "../../api/client";

interface Props { task: Task; onOpen?: (t: Task) => void; }

const PRIO_COLOR: Record<string, string> = {
  P0: "border-l-rose-500", P1: "border-l-orange-500",
  P2: "border-l-amber-500", P3: "border-l-emerald-500",
};

export function TaskCard({ task, onOpen }: Props) {
  const prio = (task.attrs.priority as string) ?? "";
  const accent = PRIO_COLOR[prio] ?? "border-l-slate-300";
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
    </motion.div>
  );
}
