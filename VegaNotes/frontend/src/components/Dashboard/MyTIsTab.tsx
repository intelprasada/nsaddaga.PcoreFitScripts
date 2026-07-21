/**
 * MyTIsTab.tsx — IC user's own turnin view (no engineer picker).
 * Also used as the "MyTIs" tab for admin users.
 */
import { useQuery } from "@tanstack/react-query";
import { api, TurninReport } from "../../api/client";
import { TurninsPanel } from "./TurninsPanel";

interface Props {
  project: string;
  range: string;
  year: number;
}

export function MyTIsTab({ project, range, year }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["dashboard-my-turnins", project, range, year],
    queryFn: () => api.dashboardTurnins(project, undefined, range, year),
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <p style={{ color: "var(--dash-mute)" }}>
        Loading your turnins (may take ~15 s on cold cache)…
      </p>
    );
  }
  if (isError || !data) {
    return <p style={{ color: "#f87171" }}>Failed to load turnin data.</p>;
  }

  if (!("turnins" in data)) {
    return <p style={{ color: "#f87171" }}>Unexpected data shape from server.</p>;
  }

  return <TurninsPanel data={data as TurninReport} />;
}
