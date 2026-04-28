import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type MeHistoryDay } from "../../api/client";

const RANGES = [7, 30, 90] as const;
type Range = typeof RANGES[number];

export function HistorySpark() {
  const [days, setDays] = useState<Range>(30);
  const { data, isLoading, error } = useQuery<MeHistoryDay[]>({
    queryKey: ["me", "history", days],
    queryFn: () => api.meHistory(days),
    staleTime: 30_000,
  });

  const max = useMemo(() => {
    if (!data || data.length === 0) return 1;
    return Math.max(1, ...data.map((d) => Math.max(d.closes, d.edits)));
  }, [data]);

  if (isLoading) return <div className="rounded border border-slate-200 p-4 text-slate-500">Loading history…</div>;
  if (error) return <div className="rounded border border-rose-200 p-4 text-rose-700">Failed to load history.</div>;
  if (!data) return null;

  const W = Math.max(1, data.length);
  const H = 60;
  const COL = 8; // px per day
  const totalW = W * COL;

  const sumCloses = data.reduce((a, d) => a + d.closes, 0);
  const sumEdits = data.reduce((a, d) => a + d.edits, 0);

  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h2 className="text-sm font-medium text-slate-700">
          History{" "}
          <span className="text-xs text-slate-500 font-normal">
            {sumCloses} close{sumCloses === 1 ? "" : "s"} · {sumEdits} note edit{sumEdits === 1 ? "" : "s"}
          </span>
        </h2>
        <div className="inline-flex rounded border border-slate-300 overflow-hidden text-xs">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setDays(r)}
              className={`px-2 py-1 ${
                r === days ? "bg-sky-100 text-sky-900" : "bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              {r}d
            </button>
          ))}
        </div>
      </div>

      {data.length === 0 ? (
        <p className="text-sm text-slate-500">No activity in this window.</p>
      ) : (
        <div className="rounded border border-slate-200 bg-white p-3 overflow-x-auto">
          <svg width={totalW} height={H + 18} role="img" aria-label="closes and edits per day">
            {data.map((d, i) => {
              const x = i * COL;
              const closesH = (d.closes / max) * H;
              const editsH = (d.edits / max) * H;
              const barW = COL - 2;
              return (
                <g key={d.date}>
                  <title>{`${d.date}: ${d.closes} closed, ${d.edits} edited`}</title>
                  <rect
                    x={x}
                    y={H - editsH}
                    width={barW / 2}
                    height={editsH}
                    fill="#bae6fd"
                  />
                  <rect
                    x={x + barW / 2}
                    y={H - closesH}
                    width={barW / 2}
                    height={closesH}
                    fill="#0369a1"
                  />
                </g>
              );
            })}
            {data.length > 0 && (
              <>
                <text x={0} y={H + 12} fontSize="9" fill="#64748b">{data[0].date}</text>
                <text
                  x={totalW}
                  y={H + 12}
                  fontSize="9"
                  fill="#64748b"
                  textAnchor="end"
                >
                  {data[data.length - 1].date}
                </text>
              </>
            )}
          </svg>
          <div className="text-[10px] text-slate-500 flex gap-3 mt-1">
            <span><span className="inline-block w-2 h-2 align-middle bg-[#0369a1] mr-1" />closes</span>
            <span><span className="inline-block w-2 h-2 align-middle bg-[#bae6fd] mr-1" />edits</span>
          </div>
        </div>
      )}
    </section>
  );
}
