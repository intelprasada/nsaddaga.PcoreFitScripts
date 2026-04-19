import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { useUI } from "../../store/ui";

export function AgendaView() {
  const { filters } = useUI();
  const { data, isLoading } = useQuery({
    queryKey: ["agenda", filters.owner],
    queryFn: () => api.agenda(filters.owner, 7),
  });
  if (isLoading) return <div className="p-6">Loading…</div>;
  if (!data) return null;
  const days = Object.keys(data.by_day).sort();
  return (
    <div className="p-4 space-y-6">
      <h1 className="text-xl font-semibold">
        Agenda — next {data.window.days} days
        {filters.owner && <span className="text-slate-500 font-normal"> · @{filters.owner}</span>}
      </h1>
      {days.length === 0 && <p className="text-slate-500">No tasks in window.</p>}
      {days.map((d) => (
        <section key={d}>
          <h2 className="text-sm font-medium text-slate-600 mb-2">{d}</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
            {data.by_day[d].map((t) => <TaskCard key={t.id} task={t} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
