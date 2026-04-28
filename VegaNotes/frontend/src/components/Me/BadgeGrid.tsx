import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type MeBadges } from "../../api/client";

function fmtDate(iso: string): string {
  // Trim to YYYY-MM-DD; full timestamp clutters the tile.
  return iso.slice(0, 10);
}

export function BadgeGrid() {
  const [showLocked, setShowLocked] = useState(true);
  const { data, isLoading, error } = useQuery<MeBadges>({
    queryKey: ["me", "badges"],
    queryFn: () => api.meBadges(),
    staleTime: 30_000,
  });
  if (isLoading) return <div className="rounded border border-slate-200 p-4 text-slate-500">Loading badges…</div>;
  if (error) return <div className="rounded border border-rose-200 p-4 text-rose-700">Failed to load badges.</div>;
  if (!data) return null;

  const earnedCount = data.earned.length;

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h2 className="text-sm font-medium text-slate-700">
          Badges{" "}
          <span className="text-xs text-slate-500 font-normal">
            {earnedCount} / {data.total_count} earned
          </span>
        </h2>
        <label className="text-xs text-slate-600 inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={showLocked}
            onChange={(e) => setShowLocked(e.target.checked)}
          />
          show locked
        </label>
      </div>

      {earnedCount === 0 && data.locked.length === 0 && data.hidden_locked_count === 0 && (
        <p className="text-sm text-slate-500">No badges available yet.</p>
      )}

      {earnedCount > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
          {data.earned.map((b) => (
            <div
              key={b.key}
              className="rounded border border-amber-300 bg-amber-50 p-3"
              title={b.description}
            >
              <div className="text-sm font-semibold text-amber-900">🏆 {b.title}</div>
              <div className="text-xs text-amber-800/80 mt-0.5 line-clamp-2">{b.description}</div>
              <div className="text-[10px] text-amber-700 mt-1">earned {fmtDate(b.awarded_at)}</div>
            </div>
          ))}
        </div>
      )}

      {showLocked && data.locked.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
          {data.locked.map((b) => (
            <div
              key={b.key}
              className="rounded border border-slate-200 bg-slate-50 p-3 opacity-80"
              title={b.description}
            >
              <div className="text-sm font-semibold text-slate-600">🔒 {b.title}</div>
              <div className="text-xs text-slate-500 mt-0.5 line-clamp-2">{b.description}</div>
              {b.progress != null && (
                <div className="mt-1.5">
                  <div className="h-1.5 bg-slate-200 rounded overflow-hidden">
                    <div
                      className="h-full bg-sky-400"
                      style={{ width: `${Math.round(Math.max(0, Math.min(1, b.progress)) * 100)}%` }}
                    />
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">
                    {Math.round(Math.max(0, Math.min(1, b.progress)) * 100)}%
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {data.hidden_locked_count > 0 && (
        <p className="text-xs text-slate-500">
          + {data.hidden_locked_count} hidden badge{data.hidden_locked_count === 1 ? "" : "s"} to discover
        </p>
      )}
    </section>
  );
}
