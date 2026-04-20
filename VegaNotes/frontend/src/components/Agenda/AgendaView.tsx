import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { useUI } from "../../store/ui";
import { formatIntelWw } from "@veganotes/parser";

type Range = { start?: string; end?: string; days?: number; label: string };

export function AgendaView() {
  const { filters } = useUI();
  const [range] = useState<Range>({ days: 7, label: "Next 7 days" });
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["agenda", filters.owner, range.start, range.end, range.days],
    queryFn: () => api.agenda(filters.owner, range.days, range.start, range.end),
  });
  if (isLoading) return <div className="p-6">Loading…</div>;
  if (!data) return null;
  const days = Object.keys(data.by_day).sort();
  return (
    <div className="p-4 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">
          Agenda — {range.label}
          {filters.owner && <span className="text-slate-500 font-normal"> · @{filters.owner}</span>}
        </h1>
        <button
          className="text-sm rounded px-3 py-1 border border-slate-300 hover:bg-slate-100"
          onClick={() => refetch()}
          title="Refresh agenda (excludes done items)"
        >↻</button>
      </div>
      {days.length === 0 && <p className="text-slate-500">No open tasks in window.</p>}
      {days.map((d) => (
        <section key={d}>
          <h2 className="text-sm font-medium text-slate-600 mb-2">
            {d} <span className="text-slate-400">· {formatIntelWw(d)}</span>
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
            {data.by_day[d].map((t) => <TaskCard key={t.id} task={t} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
