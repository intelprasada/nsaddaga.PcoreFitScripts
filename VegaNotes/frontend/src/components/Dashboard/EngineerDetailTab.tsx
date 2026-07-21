/**
 * EngineerDetailTab.tsx — per-engineer breakdown with monthly charts,
 * category donut, files table, and expandable commit list.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, DashboardData, EngineerData, DashboardFileStats } from "../../api/client";
import { Chart, DASH_COLORS, PROJECT_COLORS, destroyChart } from "./useChartJs";

interface Props {
  project: string;
  range: string;
  year: number;
  selectedEngineer: string;
  onSelectEngineer: (name: string) => void;
}

function fmtNum(n: number) {
  return (n >= 0 ? "+" : "") + n.toLocaleString();
}

function makeGradient(
  ctx: CanvasRenderingContext2D,
  area: { top: number; bottom: number },
  color: string,
): CanvasGradient | string {
  const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, color + "ff");
  g.addColorStop(1, color + "55");
  return g;
}

function buildBarChart(
  canvas: HTMLCanvasElement,
  labels: string[],
  series: { label: string; color?: string; data: number[] }[],
  opts: { stacked?: boolean; perBarColor?: boolean } = {},
): Chart {
  const cctx = canvas.getContext("2d")!;
  const stacked = !!opts.stacked;
  const perBarColor = !!opts.perBarColor;
  const datasets = series.map((s, si) => {
    const seriesColor = s.color || DASH_COLORS[si % DASH_COLORS.length];
    const bg = (context: { chart: Chart; dataIndex: number }) => {
      const { chartArea } = context.chart;
      if (!chartArea) return "rgba(59,130,246,0.4)";
      const col = perBarColor ? DASH_COLORS[context.dataIndex % DASH_COLORS.length] : seriesColor;
      return makeGradient(cctx, chartArea, col);
    };
    return {
      label: s.label,
      data: s.data,
      backgroundColor: bg as unknown as string,
      hoverBackgroundColor: perBarColor
        ? labels.map((_, i) => DASH_COLORS[i % DASH_COLORS.length])
        : seriesColor,
      borderColor: seriesColor,
      borderWidth: 0,
      borderRadius: stacked ? 4 : 8,
      borderSkipped: false as const,
      barPercentage: 0.78,
      categoryPercentage: 0.78,
      stack: stacked ? "s0" : undefined,
    };
  });
  return new Chart(canvas, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 16 } },
      plugins: { legend: { display: series.length > 1 } },
      scales: {
        x: {
          stacked,
          ticks: { color: "#c4cef0", font: { size: 13 }, autoSkip: false },
          grid: { display: false },
          border: { display: false },
        },
        y: {
          stacked,
          beginAtZero: true,
          ticks: { color: "#c4cef0", font: { size: 13 } },
          grid: { color: "rgba(162,179,217,0.12)" },
          border: { display: false },
        },
      },
    },
  });
}

function buildPieChart(canvas: HTMLCanvasElement, labels: string[], values: number[]): Chart {
  const palette = labels.map((_, i) => DASH_COLORS[i % DASH_COLORS.length]);
  return new Chart(canvas, {
    type: "doughnut",
    data: {
      labels,
      datasets: [
        {
          data: values,
          backgroundColor: palette,
          hoverBackgroundColor: palette,
          borderColor: "#121b36",
          borderWidth: 2,
          hoverOffset: 8,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "55%",
      layout: { padding: 16 },
      plugins: {
        legend: {
          position: "right",
          labels: { color: "#e6ebff", boxWidth: 14, boxHeight: 14, padding: 12 },
        },
      },
    },
  });
}

export function EngineerDetailTab({ project, range, year, selectedEngineer, onSelectEngineer }: Props) {
  const { data: gitData, isLoading, isError } = useQuery({
    queryKey: ["dashboard-data", project, range, year],
    queryFn: () => api.dashboardData(project, range, year),
    staleTime: 5 * 60 * 1000,
  });

  const engData = gitData ? (gitData as DashboardData).engineers.find((e) => e.engineer === selectedEngineer) : undefined;
  const effEng = engData ?? (gitData ? (gitData as DashboardData).engineers[0] : undefined);

  const [fileFilter, setFileFilter] = useState("");
  const [commitFilter, setCommitFilter] = useState("");
  const [expandedCommit, setExpandedCommit] = useState<string | null>(null);
  const [fileDetail, setFileDetail] = useState<{ path: string; stats: DashboardFileStats } | null>(null);

  const monthRef    = useRef<Chart | null>(null);
  const monthNetRef = useRef<Chart | null>(null);
  const pieRef      = useRef<Chart | null>(null);
  const canvasMon   = useRef<HTMLCanvasElement>(null);
  const canvasNet   = useRef<HTMLCanvasElement>(null);
  const canvasPie   = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!gitData || !effEng) return;
    const d = gitData as DashboardData;
    const projs = Object.keys(d.team_totals_by_project || {});
    const splitProjects = d.project === "ALL" && projs.length > 1;

    destroyChart(monthRef);
    destroyChart(monthNetRef);
    destroyChart(pieRef);

    if (canvasMon.current) {
      monthRef.current = splitProjects
        ? buildBarChart(
            canvasMon.current,
            d.months,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: d.months.map((m) => ((((effEng.per_project || {})[p] || {}) as EngineerData).monthly || {})[m]?.commits || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasMon.current,
            d.months,
            [{ label: "Commits", data: d.months.map((m) => (effEng.monthly[m] && effEng.monthly[m].commits) || 0) }],
            { perBarColor: true },
          );
    }

    if (canvasNet.current) {
      monthNetRef.current = splitProjects
        ? buildBarChart(
            canvasNet.current,
            d.months,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: d.months.map((m) => ((((effEng.per_project || {})[p] || {}) as EngineerData).monthly || {})[m]?.net || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasNet.current,
            d.months,
            [{ label: "Net Lines", data: d.months.map((m) => (effEng.monthly[m] && effEng.monthly[m].net) || 0) }],
            { perBarColor: true },
          );
    }

    if (canvasPie.current) {
      const cats = d.categories;
      pieRef.current = buildPieChart(
        canvasPie.current,
        cats,
        cats.map((c) => effEng.categories[c] || 0),
      );
    }

    return () => {
      destroyChart(monthRef);
      destroyChart(monthNetRef);
      destroyChart(pieRef);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gitData, effEng?.engineer]);

  if (isLoading) return <p style={{ color: "var(--dash-mute)" }}>Loading…</p>;
  if (isError || !gitData) return <p style={{ color: "#f87171" }}>Failed to load data.</p>;

  const d = gitData as DashboardData;

  const fileEntries = effEng
    ? Object.entries(effEng.files)
        .map(([path, v]) => ({ path, stats: v }))
        .filter(({ path }) => !fileFilter || path.toLowerCase().includes(fileFilter.toLowerCase()))
        .sort((a, b) => b.stats.commits - a.stats.commits)
    : [];

  const commits = effEng
    ? (effEng.commits || []).filter(
        (c) =>
          !commitFilter ||
          c.subject.toLowerCase().includes(commitFilter.toLowerCase()) ||
          c.sha.toLowerCase().includes(commitFilter.toLowerCase()),
      )
    : [];

  return (
    <div>
      {/* Engineer picker */}
      <div className="dash-card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 600, color: "var(--dash-ink)" }}>Engineer:</span>
          <select
            className="dash-select"
            style={{ minWidth: 240 }}
            value={effEng?.engineer || ""}
            onChange={(e) => onSelectEngineer(e.target.value)}
          >
            {d.engineers.map((e) => (
              <option key={e.engineer} value={e.engineer}>
                {e.engineer}{e.idsid ? ` (${e.idsid})` : ""} — {e.total} commit{e.total === 1 ? "" : "s"}
              </option>
            ))}
          </select>
          {effEng && (
            <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>
              Project: {d.project} • {d.window.label} • {d.window.since} → {d.window.until}
            </span>
          )}
        </div>
      </div>

      {/* KPIs */}
      {effEng && (
        <div className="dash-kpis">
          {[
            ["Engineer", effEng.engineer],
            ["IDSID / WWID", `${effEng.idsid || "–"} / ${effEng.wwid || "–"}`],
            ["Commits", effEng.total.toLocaleString()],
            ["Net Lines", fmtNum(effEng.net_lines)],
            ["Avg / Median", `${effEng.avg_lines} / ${effEng.median_lines}`],
            ["% ≤ Median", `${effEng.pct_at_or_below}%`],
            ["Pattern", effEng.pattern || "–"],
          ].map(([lbl, val]) => (
            <div key={lbl} className="dash-kpi">
              <div className="lbl">{lbl}</div>
              <div className="val" style={{ fontSize: 16 }}>{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Monthly charts */}
      <div className="dash-grid2">
        <div className="dash-card">
          <h3>Monthly Commits{effEng ? ` – ${effEng.engineer.split(" ")[0]}` : ""}</h3>
          <div style={{ height: 240 }}>
            <canvas ref={canvasMon} />
          </div>
        </div>
        <div className="dash-card">
          <h3>Net Lines per Month{effEng ? ` – ${effEng.engineer.split(" ")[0]}` : ""}</h3>
          <div style={{ height: 240 }}>
            <canvas ref={canvasNet} />
          </div>
        </div>
      </div>

      {/* Category donut + Files table */}
      <div className="dash-grid2">
        <div className="dash-card">
          <h3>Commit-Type Mix{effEng ? ` – ${effEng.engineer.split(" ")[0]}` : ""}</h3>
          <div style={{ height: 260 }}>
            <canvas ref={canvasPie} />
          </div>
        </div>
        <div className="dash-card">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>Files Modified</h3>
            <span style={{ color: "var(--dash-mute)", fontSize: 12, flex: 1 }}>
              {effEng ? `${fileEntries.length} file${fileEntries.length === 1 ? "" : "s"} · click for detail` : ""}
            </span>
            <input
              className="dash-input"
              placeholder="Filter files…"
              style={{ minWidth: 160 }}
              value={fileFilter}
              onChange={(e) => setFileFilter(e.target.value)}
            />
          </div>
          <div style={{ maxHeight: 280, overflowY: "auto" }}>
            <table className="dash-tbl">
              <thead>
                <tr>
                  <th style={{ width: "52%" }}>File Path</th>
                  <th>Commits</th>
                  <th>Added</th>
                  <th>Deleted</th>
                  <th>Net</th>
                </tr>
              </thead>
              <tbody>
                {fileEntries.map(({ path, stats }) => (
                  <tr
                    key={path}
                    onClick={() =>
                      setFileDetail(fileDetail?.path === path ? null : { path, stats })
                    }
                    style={{ cursor: "pointer" }}
                  >
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12, wordBreak: "break-all" }}>{path}</td>
                    <td>{stats.commits}</td>
                    <td style={{ color: "#34d399" }}>+{stats.add.toLocaleString()}</td>
                    <td style={{ color: "#f87171" }}>-{stats.del.toLocaleString()}</td>
                    <td style={{ color: stats.net >= 0 ? "#34d399" : "#f87171" }}>{fmtNum(stats.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* File detail panel */}
      {fileDetail && (
        <div className="dash-card" style={{ marginTop: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
            <h3 style={{ margin: 0, fontFamily: "ui-monospace, monospace", fontSize: 13 }}>
              Commits touching {fileDetail.path}
            </h3>
            <button className="dash-btn" style={{ padding: "3px 10px", marginLeft: "auto" }} onClick={() => setFileDetail(null)}>
              ✕ Close
            </button>
          </div>
          <div style={{ maxHeight: 300, overflowY: "auto" }}>
            <table className="dash-tbl">
              <thead>
                <tr>
                  <th>Commit</th>
                  <th>Date</th>
                  <th>Subject</th>
                  <th>Added</th>
                  <th>Deleted</th>
                  <th>Net</th>
                </tr>
              </thead>
              <tbody>
                {(fileDetail.stats.commits_list || []).map((c, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>{c.sha}</td>
                    <td>{c.date}</td>
                    <td>{c.subject}</td>
                    <td style={{ color: "#34d399" }}>+{c.add.toLocaleString()}</td>
                    <td style={{ color: "#f87171" }}>-{c.del.toLocaleString()}</td>
                    <td style={{ color: c.net >= 0 ? "#34d399" : "#f87171" }}>{fmtNum(c.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Commits table */}
      <div className="dash-card" style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>Commits{effEng ? ` – ${effEng.engineer}` : ""}</h3>
          <span style={{ color: "var(--dash-mute)", fontSize: 12, flex: 1 }}>
            {commits.length} commit{commits.length === 1 ? "" : "s"} · click to expand files
          </span>
          <input
            className="dash-input"
            placeholder="Filter commits (subject or SHA)…"
            style={{ minWidth: 240 }}
            value={commitFilter}
            onChange={(e) => setCommitFilter(e.target.value)}
          />
        </div>
        <div style={{ maxHeight: 480, overflowY: "auto" }}>
          <table className="dash-tbl">
            <thead>
              <tr>
                <th style={{ width: "12%" }}>Commit</th>
                <th style={{ width: "9%" }}>Date</th>
                <th style={{ width: "43%" }}>Subject</th>
                <th>Files</th>
                <th>Added</th>
                <th>Deleted</th>
                <th>Net</th>
              </tr>
            </thead>
            <tbody>
              {commits.map((c) => (
                <>
                  <tr
                    key={c.sha}
                    onClick={() => setExpandedCommit(expandedCommit === c.sha ? null : c.sha)}
                    style={{ cursor: "pointer" }}
                  >
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
                      {c.sha}
                      {c.project && (
                        <span
                          className="dash-pill"
                          style={{ padding: "1px 6px", fontSize: 11, marginLeft: 4 }}
                        >
                          {c.project}
                        </span>
                      )}
                    </td>
                    <td>{c.date}</td>
                    <td>{c.subject}</td>
                    <td>{c.files}</td>
                    <td style={{ color: "#34d399" }}>+{c.add.toLocaleString()}</td>
                    <td style={{ color: "#f87171" }}>-{c.del.toLocaleString()}</td>
                    <td style={{ color: c.net >= 0 ? "#34d399" : "#f87171" }}>{fmtNum(c.net)}</td>
                  </tr>
                  {expandedCommit === c.sha && (
                    <tr key={`${c.sha}-detail`}>
                      <td
                        colSpan={7}
                        style={{
                          background: "rgba(59,130,246,0.06)",
                          padding: "10px 14px",
                        }}
                      >
                        <div style={{ fontSize: 12, color: "var(--dash-mute)", marginBottom: 6 }}>
                          {c.files} file{c.files === 1 ? "" : "s"} in commit{" "}
                          <code style={{ fontFamily: "ui-monospace", color: "var(--dash-ink)" }}>
                            {c.full_sha || c.sha}
                          </code>
                        </div>
                        <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
                          <thead>
                            <tr>
                              <th style={{ width: "64%", textAlign: "left", color: "var(--dash-mute)", padding: "4px 8px" }}>File</th>
                              <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "4px 8px" }}>Added</th>
                              <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "4px 8px" }}>Deleted</th>
                              <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "4px 8px" }}>Net</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(c.file_stats || []).map((f, fi) => {
                              const net = f.add - f.del;
                              return (
                                <tr key={fi}>
                                  <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12, wordBreak: "break-all", padding: "3px 8px" }}>{f.path}</td>
                                  <td style={{ color: "#34d399", padding: "3px 8px" }}>+{f.add.toLocaleString()}</td>
                                  <td style={{ color: "#f87171", padding: "3px 8px" }}>-{f.del.toLocaleString()}</td>
                                  <td style={{ color: net >= 0 ? "#34d399" : "#f87171", padding: "3px 8px" }}>{fmtNum(net)}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
