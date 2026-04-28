import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type MeActivityEvent } from "../../api/client";

const KIND_OPTIONS = [
  "",
  "task.created",
  "task.closed",
  "task.status",
  "note.created",
  "note.edited",
] as const;

function fmtTs(iso: string): string {
  // Show local-readable date+time. The server emits ISO with no zone for
  // naive UTC; we display whatever the browser parses.
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function ActivityList() {
  const [kind, setKind] = useState<string>("");
  const [limit, setLimit] = useState<number>(50);
  const { data, isLoading, error } = useQuery<MeActivityEvent[]>({
    queryKey: ["me", "activity", kind, limit],
    queryFn: () => api.meActivity({ kind: kind || undefined, limit }),
    staleTime: 15_000,
  });

  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h2 className="text-sm font-medium text-slate-700">Recent activity</h2>
        <div className="flex items-center gap-2 text-xs">
          <label className="text-slate-600">
            kind{" "}
            <select
              className="border border-slate-300 rounded px-1 py-0.5"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {KIND_OPTIONS.map((k) => (
                <option key={k} value={k}>{k || "all"}</option>
              ))}
            </select>
          </label>
          <label className="text-slate-600">
            show{" "}
            <select
              className="border border-slate-300 rounded px-1 py-0.5"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {[25, 50, 100, 200].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {isLoading && <div className="text-sm text-slate-500">Loading…</div>}
      {error && <div className="text-sm text-rose-700">Failed to load activity.</div>}
      {data && data.length === 0 && <p className="text-sm text-slate-500">No events.</p>}
      {data && data.length > 0 && (
        <div className="rounded border border-slate-200 bg-white overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="text-left px-2 py-1.5">when</th>
                <th className="text-left px-2 py-1.5">kind</th>
                <th className="text-left px-2 py-1.5">ref</th>
              </tr>
            </thead>
            <tbody>
              {data.map((ev) => (
                <tr key={ev.id} className="border-t border-slate-100">
                  <td className="px-2 py-1 text-slate-600 whitespace-nowrap">{fmtTs(ev.ts)}</td>
                  <td className="px-2 py-1 font-mono text-slate-800">{ev.kind}</td>
                  <td className="px-2 py-1 font-mono text-slate-500">{ev.ref ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
