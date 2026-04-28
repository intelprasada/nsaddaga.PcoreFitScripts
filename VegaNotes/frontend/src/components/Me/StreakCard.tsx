import { useQuery } from "@tanstack/react-query";
import { api, type MeStreak } from "../../api/client";

export function StreakCard() {
  const { data, isLoading, error } = useQuery<MeStreak>({
    queryKey: ["me", "streak"],
    queryFn: () => api.meStreak(),
    staleTime: 30_000,
  });
  if (isLoading) return <div className="rounded border border-slate-200 p-4 text-slate-500">Loading streak…</div>;
  if (error) return <div className="rounded border border-rose-200 p-4 text-rose-700">Failed to load streak.</div>;
  if (!data) return null;

  return (
    <section className="rounded border border-slate-200 bg-white p-4 flex items-center gap-6 flex-wrap">
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">Current streak</div>
        <div className="text-3xl font-semibold text-sky-700">
          {data.current_streak_days}
          <span className="text-base font-normal text-slate-500"> day{data.current_streak_days === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">Longest</div>
        <div className="text-2xl font-semibold text-slate-800">
          {data.longest_streak_days}
          <span className="text-base font-normal text-slate-500"> d</span>
        </div>
      </div>
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">Rest tokens</div>
        <div className="text-2xl font-semibold text-slate-800">{data.rest_tokens_remaining}<span className="text-base font-normal text-slate-500"> /14d</span></div>
      </div>
      <div className="ml-auto text-xs text-slate-400">as of {data.as_of}</div>
    </section>
  );
}
