import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useUI } from "../../store/ui";
import { ChipBar } from "./ChipBar";
import { SavedViews } from "./SavedViews";

export function FilterBar() {
  const { filters, patchFilters, clearFilters } = useUI();
  const { data: users } = useQuery({ queryKey: ["users", "withDisplay"], queryFn: api.usersWithDisplay });
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const { data: features } = useQuery({ queryKey: ["features"], queryFn: api.features });

  return (
    <div className="flex flex-wrap items-center gap-2 border-b bg-white p-3">
      <select className="rounded border px-2 py-1 text-sm" value={filters.owner ?? ""}
        onChange={(e) => patchFilters({ owner: e.target.value || undefined })}>
        <option value="">all owners</option>
        {users?.map((u) => (
          <option key={u.name} value={u.name} title={`@${u.name}`}>
            {u.display !== u.name ? u.display : `@${u.name}`}
          </option>
        ))}
      </select>
      <select className="rounded border px-2 py-1 text-sm" value={filters.project ?? ""}
        onChange={(e) => patchFilters({ project: e.target.value || undefined })}>
        <option value="">all projects</option>
        {projects?.map((p) => <option key={p.name} value={p.name}>#{p.name}</option>)}
      </select>
      <select className="rounded border px-2 py-1 text-sm" value={filters.feature ?? ""}
        onChange={(e) => patchFilters({ feature: e.target.value || undefined })}>
        <option value="">all features</option>
        {features?.map((f) => <option key={f} value={f}>★{f}</option>)}
      </select>
      <label className="flex items-center gap-1 text-sm">
        <input type="checkbox" checked={!!filters.hide_done}
          onChange={(e) => patchFilters({ hide_done: e.target.checked })} />
        hide done
      </label>
      <input className="rounded border px-2 py-1 text-sm w-[160px]"
        placeholder="search title…" value={filters.q ?? ""}
        onChange={(e) => patchFilters({ q: e.target.value || undefined })} />
      <ChipBar />
      <SavedViews />
      <button className="text-sm text-slate-500 hover:text-slate-900"
        onClick={() => clearFilters()}>reset</button>
    </div>
  );
}
