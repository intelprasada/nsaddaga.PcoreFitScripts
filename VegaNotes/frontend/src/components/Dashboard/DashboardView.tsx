/**
 * DashboardView.tsx — main container for the team-performance dashboard.
 *
 * Admin users see four tabs: Team Overview | Engineer Detail | Turnins (TI) | MyTIs
 * IC (non-admin) users see only their own turnins (MyTIs) with no tab bar.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { TeamOverviewTab } from "./TeamOverviewTab";
import { EngineerDetailTab } from "./EngineerDetailTab";
import { TurninsTab } from "./TurninsTab";
import { MyTIsTab } from "./MyTIsTab";

const DASHBOARD_CSS = `
.dashboard-root {
  --dash-bg:      #0a0f1f;
  --dash-bg2:     #0d1428;
  --dash-panel:   #121b36;
  --dash-panel2:  #17224a;
  --dash-ink:     #f5f8ff;
  --dash-ink2:    #d7deef;
  --dash-mute:    #a2b3d9;
  --dash-border:  #263863;
  --dash-border2: #32467a;
  --dash-accent:  #3b82f6;
  --dash-green:   #10b981;
  --dash-red:     #ef4444;
  --dash-yellow:  #f59e0b;
}
.dash-tab { background: transparent; color: var(--dash-mute); border: none;
  padding: 7px 14px; border-radius: 8px; font-size: 13px; font-weight: 600;
  cursor: pointer; letter-spacing: .2px; transition: color .15s; }
.dash-tab:hover { color: var(--dash-ink); }
.dash-tab.is-active {
  background: linear-gradient(180deg, #1f3170 0%, #223880 100%);
  color: var(--dash-ink);
  box-shadow: 0 0 0 1px var(--dash-accent) inset;
}
.dash-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 18px; }
.dash-kpi { background: linear-gradient(180deg, var(--dash-panel) 0%, var(--dash-bg2) 100%);
  border: 1px solid var(--dash-border); border-radius: 12px; padding: 16px; }
.dash-kpi .lbl { color: var(--dash-mute); font-size: 11px; text-transform: uppercase;
  letter-spacing: .6px; font-weight: 500; }
.dash-kpi .val { font-size: 22px; font-weight: 600; margin-top: 6px; color: var(--dash-ink); word-break: break-word; }
.dash-card { background: var(--dash-panel); border: 1px solid var(--dash-border);
  border-radius: 12px; padding: 16px;
  box-shadow: 0 1px 0 rgba(255,255,255,.02) inset, 0 8px 20px rgba(0,0,0,.25); }
.dash-card h3 { margin: 0 0 12px 0; font-size: 14px; color: var(--dash-ink); font-weight: 600; }
.dash-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }
.dash-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
.dash-pill { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px;
  background: rgba(96,165,250,.10); color: var(--dash-ink2); border: 1px solid var(--dash-border2); }
.dash-tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.dash-tbl th, .dash-tbl td { border-bottom: 1px solid var(--dash-border); padding: 9px 8px; text-align: left; }
.dash-tbl th { color: var(--dash-mute); font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: .5px; background: var(--dash-panel); position: sticky; top: 0; }
.dash-tbl tr:hover td { background: rgba(96,165,250,.07); cursor: pointer; }
.dash-tbl tr.sel td { background: rgba(96,165,250,.18); }
.dash-select, .dash-input {
  background: var(--dash-panel2); color: var(--dash-ink); border: 1px solid var(--dash-border2);
  border-radius: 8px; padding: 7px 11px; font-size: 13px; }
.dash-select:hover, .dash-input:hover { border-color: var(--dash-accent); }
.dash-btn { background: var(--dash-panel2); color: var(--dash-ink); border: 1px solid var(--dash-border2);
  border-radius: 8px; padding: 7px 12px; font-size: 13px; cursor: pointer; font-weight: 500; }
.dash-btn:hover { border-color: var(--dash-accent); }
.dash-btn.is-active { background: linear-gradient(180deg, #1f3170 0%, #223880 100%); border-color: var(--dash-accent); }
@media (max-width: 1000px) { .dash-grid, .dash-grid2 { grid-template-columns: 1fr; } }
`;

type TabId = "team" | "eng" | "ti" | "myti";

export function DashboardView() {
  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me(),
  });

  const [activeTab, setActiveTab] = useState<TabId>("team");
  const [project, setProject] = useState("ALL");
  const [range, setRange] = useState("H1");
  const [year, setYear] = useState<number>(new Date().getFullYear());
  const [selectedEngineer, setSelectedEngineer] = useState<string>("");

  if (meLoading) {
    return (
      <div style={{ padding: 32, color: "#a2b3d9", background: "#0a0f1f", minHeight: "100vh" }}>
        Loading…
      </div>
    );
  }

  const isAdmin = !!me?.is_admin;

  const controls = (
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>Window:</span>
      <select
        className="dash-select"
        value={range}
        onChange={(e) => setRange(e.target.value)}
      >
        <option value="YTD">Year to Date</option>
        <option value="MTD">This Month</option>
        <option value="H1">H1 (Jan–Jun)</option>
        <option value="H2">H2 (Jul–Dec)</option>
        <option value="FY">Full Year</option>
      </select>
      <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>Year:</span>
      <input
        className="dash-input"
        type="number"
        min={2020}
        max={2099}
        style={{ width: 74 }}
        value={year}
        onChange={(e) => setYear(Number(e.target.value) || new Date().getFullYear())}
      />
      <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>Project:</span>
      <select
        className="dash-select"
        value={project}
        onChange={(e) => setProject(e.target.value)}
      >
        <option value="ALL">Both (GFC + JNC)</option>
        <option value="GFC">GFC</option>
        <option value="JNC">JNC</option>
      </select>
    </div>
  );

  return (
    <>
      <style>{DASHBOARD_CSS}</style>
      <div
        className="dashboard-root"
        style={{
          background: "var(--dash-bg)",
          color: "var(--dash-ink)",
          minHeight: "100vh",
          fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          fontSize: 14,
        }}
      >
        {/* Header */}
        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 5,
            padding: "14px 24px",
            background: "rgba(10,15,32,0.92)",
            backdropFilter: "blur(8px)",
            borderBottom: "1px solid var(--dash-border)",
            display: "flex",
            alignItems: "center",
            gap: 18,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 18, fontWeight: 600 }}>
            📊 {isAdmin ? "TeamHub" : "My Turnins"}
          </span>
          {isAdmin && (
            <div
              style={{
                display: "flex",
                gap: 4,
                background: "var(--dash-panel2)",
                border: "1px solid var(--dash-border2)",
                borderRadius: 10,
                padding: 4,
              }}
              role="tablist"
            >
              {(
                [
                  { id: "team" as TabId, label: "Team Overview" },
                  { id: "eng"  as TabId, label: "Engineer Detail" },
                  { id: "ti"   as TabId, label: "Turnins (TI)" },
                  { id: "myti" as TabId, label: "MyTIs" },
                ] as const
              ).map(({ id, label }) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={activeTab === id}
                  className={`dash-tab${activeTab === id ? " is-active" : ""}`}
                  onClick={() => setActiveTab(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <div style={{ flex: 1 }} />
          {controls}
        </div>

        {/* Tab content */}
        <div style={{ padding: "20px 24px 44px" }}>
          {isAdmin && activeTab === "team" && (
            <TeamOverviewTab
              project={project}
              range={range}
              year={year}
              onSelectEngineer={(name) => {
                setSelectedEngineer(name);
                setActiveTab("eng");
              }}
              onSelectEngineerTI={(name) => {
                setSelectedEngineer(name);
                setActiveTab("ti");
              }}
            />
          )}
          {isAdmin && activeTab === "eng" && (
            <EngineerDetailTab
              project={project}
              range={range}
              year={year}
              selectedEngineer={selectedEngineer}
              onSelectEngineer={setSelectedEngineer}
            />
          )}
          {isAdmin && activeTab === "ti" && (
            <TurninsTab
              project={project}
              range={range}
              year={year}
              selectedEngineer={selectedEngineer}
              onSelectEngineer={setSelectedEngineer}
            />
          )}
          {(activeTab === "myti" || !isAdmin) && (
            <MyTIsTab project={project} range={range} year={year} />
          )}
        </div>
      </div>
    </>
  );
}
