import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";

/** Read+write the caller's IANA timezone via /api/me. Free-text; the
 * server validates against the IANA database and rejects unknown zones. */
export function TZSettings() {
  const qc = useQueryClient();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.me() });
  const [draft, setDraft] = useState<string>("");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (tz: string) => api.setMyTz(tz),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["me", "stats"] });
      qc.invalidateQueries({ queryKey: ["me", "streak"] });
      qc.invalidateQueries({ queryKey: ["me", "history"] });
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : "Failed to save timezone");
    },
  });

  const current = me?.tz ?? "UTC";

  return (
    <section className="rounded border border-slate-200 bg-white p-3 flex items-center gap-3 text-sm flex-wrap">
      <span className="text-slate-600">Timezone:</span>
      {!editing ? (
        <>
          <code className="text-slate-800">{current}</code>
          <button
            type="button"
            className="text-xs text-sky-700 hover:underline"
            onClick={() => { setDraft(current === "UTC" ? "" : current); setEditing(true); }}
          >
            change
          </button>
        </>
      ) : (
        <>
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. America/Los_Angeles (empty = UTC)"
            className="border border-slate-300 rounded px-2 py-1 text-xs w-72"
            autoFocus
          />
          <button
            type="button"
            className="text-xs rounded border border-sky-400 bg-sky-50 text-sky-800 px-2 py-1"
            onClick={() => mut.mutate(draft.trim())}
            disabled={mut.isPending}
          >
            {mut.isPending ? "saving…" : "save"}
          </button>
          <button
            type="button"
            className="text-xs text-slate-600 hover:underline"
            onClick={() => { setEditing(false); setError(null); }}
          >
            cancel
          </button>
        </>
      )}
      <span className="text-xs text-slate-500 ml-auto">
        Streaks roll over at local midnight in your timezone.
      </span>
      {error && <div className="basis-full text-xs text-rose-700">{error}</div>}
    </section>
  );
}
