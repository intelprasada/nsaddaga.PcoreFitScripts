/**
 * TurninsTab.tsx — Admin view of a single engineer's turnininfo data.
 *
 * Includes engineer picker, KPI chips, filter bar, and expandable turnin rows
 * with commit/file sub-tables.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, TurninReport, TurninRecord } from "../../api/client";
import { TurninsPanel } from "./TurninsPanel";

interface Props {
  project: string;
  range: string;
  year: number;
  forceKey: number;
  selectedEngineer: string;
  onSelectEngineer: (name: string) => void;
}

export function TurninsTab({ project, range, year, forceKey, selectedEngineer, onSelectEngineer }: Props) {
  const force = forceKey > 0;
  const { data: roster = [] } = useQuery({
    queryKey: ["dashboard-roster"],
    queryFn: () => api.dashboardRoster(),
    staleTime: 60 * 60 * 1000,
  });

  // Default to first roster entry if nothing selected
  const eff = selectedEngineer || roster[0] || "";

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["dashboard-turnins-eng", eff, project, range, year, forceKey],
    queryFn: () => api.dashboardTurnins(project, eff, range, year, undefined, undefined, force),
    enabled: !!eff,
    staleTime: force ? 0 : 5 * 60 * 1000,
  });

  return (
    <div>
      {/* Engineer picker */}
      <div className="dash-card" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ color: "var(--dash-mute)", fontWeight: 600, fontSize: 13 }}>Engineer:</label>
          <select
            className="dash-select"
            style={{ minWidth: 300 }}
            value={eff}
            onChange={(e) => onSelectEngineer(e.target.value)}
          >
            {roster.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <button className="dash-btn" onClick={() => void refetch()}>↻ Reload</button>
          <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>
            Cached 24 h; first load may take ~15 s while HDK env sources
          </span>
        </div>
      </div>

      {isLoading && <p style={{ color: "var(--dash-mute)" }}>Loading turnins (may take ~15 s on cold cache)…</p>}
      {isError && <p style={{ color: "#f87171" }}>Failed to load turnin data.</p>}
      {data && "turnins" in data && (
        <TurninsPanel data={data as TurninReport} />
      )}
    </div>
  );
}
