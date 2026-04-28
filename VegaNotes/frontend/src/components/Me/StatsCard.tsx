import { useQuery } from "@tanstack/react-query";
import { api, type MeStats } from "../../api/client";

function pct(n: number | null): string {
  if (n == null) return "—";
  return `${Math.round(n * 100)}%`;
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-2xl font-semibold text-slate-800 leading-tight">{value}</div>
      {hint && <div className="text-xs text-slate-500 mt-0.5">{hint}</div>}
    </div>
  );
}

export function StatsCard() {
  const { data, isLoading, error } = useQuery<MeStats>({
    queryKey: ["me", "stats"],
    queryFn: () => api.meStats(),
    staleTime: 30_000,
  });
  if (isLoading) return <div className="rounded border border-slate-200 p-4 text-slate-500">Loading stats…</div>;
  if (error) return <div className="rounded border border-rose-200 p-4 text-rose-700">Failed to load stats.</div>;
  if (!data) return null;

  const closes = data.tasks_closed;
  const notes = data.notes_touched;
  const onTimeHint = data.on_time_sample_30d
    ? `${data.on_time_sample_30d} sample${data.on_time_sample_30d === 1 ? "" : "s"} (30d)`
    : "no ETA hits in last 30d";
  const byKindEntries = Object.entries(data.by_kind).sort((a, b) => b[1] - a[1]);

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-slate-700">Stats</h2>
        <span className="text-xs text-slate-500">as of {data.as_of} · {data.tz}</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="Closed today" value={closes.today} />
        <Stat label="Closed 7d" value={closes.week} />
        <Stat label="Closed 30d" value={closes.month} />
        <Stat label="Closed lifetime" value={closes.lifetime} />
        <Stat label="Notes touched 7d" value={notes.week} />
        <Stat label="Notes touched 30d" value={notes.month} />
        <Stat label="On-time ETA (30d)" value={pct(data.on_time_eta_rate_30d)} hint={onTimeHint} />
        <Stat label="Favorite project (30d)" value={data.favorite_project_30d ?? "—"} />
      </div>
      {byKindEntries.length > 0 && (
        <div className="text-xs text-slate-600">
          <span className="text-slate-500">By kind:</span>{" "}
          {byKindEntries.map(([k, n], i) => (
            <span key={k}>
              {i > 0 && <span className="text-slate-300"> · </span>}
              <span className="font-medium">{k}</span> {n}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
