/**
 * TeamOverviewTab.tsx — Team Overview panel.
 *
 * Shows KPI chips, stacked bar charts (commits/turnins), a category donut,
 * monthly chart, and a leaderboard table.  The "Commits / Turnins" sub-toggle
 * switches between git data and turnininfo data.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, DashboardData, TeamTurninsReport } from "../../api/client";
import { Chart, DASH_COLORS, PROJECT_COLORS, destroyChart } from "./useChartJs";

interface Props {
  project: string;
  range: string;
  year: number;
  onSelectEngineer: (name: string) => void;
  onSelectEngineerTI: (name: string) => void;
}

function fmtNum(n: number) {
  return (n >= 0 ? "+" : "") + n.toLocaleString();
}

function makeGradient(
  ctx: CanvasRenderingContext2D,
  area: { top: number; bottom: number },
  color: string,
): CanvasGradient | string {
  if (!area) return color;
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
      layout: { padding: { top: 22 } },
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

function buildPieChart(
  canvas: HTMLCanvasElement,
  labels: string[],
  values: number[],
): Chart {
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
      layout: { padding: 24 },
      plugins: {
        legend: {
          position: "right",
          labels: {
            color: "#e6ebff",
            boxWidth: 14,
            boxHeight: 14,
            padding: 14,
            generateLabels(chart) {
              const ds = chart.data.datasets[0];
              const total = (ds.data as number[]).reduce((a, b) => a + (b || 0), 0) || 1;
              return (chart.data.labels as string[]).map((lbl, i) => {
                const v = (ds.data as number[])[i] || 0;
                const pct = ((100 * v) / total).toFixed(0);
                return {
                  text: `${lbl}  ${v} (${pct}%)`,
                  fillStyle: (ds.backgroundColor as string[])[i],
                  strokeStyle: (ds.backgroundColor as string[])[i],
                  hidden: false,
                  index: i,
                };
              });
            },
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const total = (ctx.dataset.data as number[]).reduce((a, b) => a + b, 0) || 1;
              const pct = ((100 * (ctx.parsed as number)) / total).toFixed(1);
              return ` ${ctx.label}: ${ctx.parsed} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

export function TeamOverviewTab({ project, range, year, onSelectEngineer, onSelectEngineerTI }: Props) {
  const [subView, setSubView] = useState<"commits" | "turnins">("commits");

  const { data: gitData, isLoading: gitLoading, isError: gitError, error: gitErrorObj } = useQuery({
    queryKey: ["dashboard-data", project, range, year],
    queryFn: () => api.dashboardData(project, range, year),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const { data: tiData, isLoading: tiLoading } = useQuery({
    queryKey: ["dashboard-team-turnins", project, range, year],
    queryFn: () => api.dashboardTurnins(project, undefined, range, year),
    enabled: subView === "turnins",
    staleTime: 5 * 60 * 1000,
  });

  const commitsRef = useRef<Chart | null>(null);
  const linesRef   = useRef<Chart | null>(null);
  const pieRef     = useRef<Chart | null>(null);
  const monthRef   = useRef<Chart | null>(null);

  const canvasCommits = useRef<HTMLCanvasElement>(null);
  const canvasLines   = useRef<HTMLCanvasElement>(null);
  const canvasPie     = useRef<HTMLCanvasElement>(null);
  const canvasMonth   = useRef<HTMLCanvasElement>(null);

  // Commits view charts
  useEffect(() => {
    if (!gitData || subView !== "commits") return;
    const d: DashboardData = gitData as DashboardData;
    const engs = d.engineers;
    const labels = engs.map((e) => e.engineer.split(" ").slice(0, 2).join(" "));
    const projs = Object.keys(d.team_totals_by_project || {});
    const splitProjects = d.project === "ALL" && projs.length > 1;

    destroyChart(commitsRef);
    destroyChart(linesRef);
    destroyChart(pieRef);
    destroyChart(monthRef);

    if (canvasCommits.current) {
      commitsRef.current = splitProjects
        ? buildBarChart(
            canvasCommits.current,
            labels,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: engs.map((e) => ((e.per_project || {})[p] || {}).total || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasCommits.current,
            labels,
            [{ label: "Commits", data: engs.map((e) => e.total) }],
            { perBarColor: true },
          );
    }

    if (canvasLines.current) {
      linesRef.current = splitProjects
        ? buildBarChart(
            canvasLines.current,
            labels,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: engs.map((e) => ((e.per_project || {})[p] || {}).net_lines || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasLines.current,
            labels,
            [{ label: "Net Lines", data: engs.map((e) => e.net_lines) }],
            { perBarColor: true },
          );
    }

    if (canvasPie.current) {
      const cats = d.categories;
      pieRef.current = buildPieChart(
        canvasPie.current,
        cats,
        cats.map((c) => d.team_categories[c] || 0),
      );
    }

    if (canvasMonth.current) {
      monthRef.current = splitProjects
        ? buildBarChart(
            canvasMonth.current,
            d.months,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: d.months.map((m) => (d.team_monthly_by_project[p] || {})[m] || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasMonth.current,
            d.months,
            [{ label: "Team Commits", data: d.months.map((m) => d.team_monthly[m] || 0) }],
            { perBarColor: true },
          );
    }

    return () => {
      destroyChart(commitsRef);
      destroyChart(linesRef);
      destroyChart(pieRef);
      destroyChart(monthRef);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gitData, subView]);

  // Turnins view charts
  useEffect(() => {
    if (!tiData || subView !== "turnins") return;
    if (!("engineers" in tiData)) return;
    const td = tiData as TeamTurninsReport;
    const engs = [...td.engineers].sort((a, b) => (b.released || 0) - (a.released || 0));
    const labels = engs.map((e) => e.engineer.split(" ").slice(0, 2).join(" "));
    const projs = Object.keys(td.team_totals_by_project || {});
    const splitProjects = td.project === "ALL" && projs.length > 1;

    destroyChart(commitsRef);
    destroyChart(linesRef);
    destroyChart(pieRef);
    destroyChart(monthRef);

    if (canvasCommits.current) {
      commitsRef.current = splitProjects
        ? buildBarChart(
            canvasCommits.current,
            labels,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: engs.map((e) => (e.per_project[p] || {}).released || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasCommits.current,
            labels,
            [{ label: "Released Turnins", data: engs.map((e) => e.released || 0) }],
            { perBarColor: true },
          );
    }

    if (canvasLines.current) {
      linesRef.current = splitProjects
        ? buildBarChart(
            canvasLines.current,
            labels,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: engs.map((e) => (e.per_project[p] || {}).released_files || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasLines.current,
            labels,
            [{ label: "Files (released)", data: engs.map((e) => e.released_files || 0) }],
            { perBarColor: true },
          );
    }

    if (canvasPie.current) {
      const stats = Object.keys(td.status_counts || {});
      pieRef.current = buildPieChart(
        canvasPie.current,
        stats,
        stats.map((s) => td.status_counts[s] || 0),
      );
    }

    if (canvasMonth.current) {
      monthRef.current = splitProjects
        ? buildBarChart(
            canvasMonth.current,
            td.months,
            projs.map((p) => ({
              label: p,
              color: PROJECT_COLORS[p] || DASH_COLORS[0],
              data: td.months.map((m) => (td.team_monthly_released_by_project[p] || {})[m] || 0),
            })),
            { stacked: true },
          )
        : buildBarChart(
            canvasMonth.current,
            td.months,
            [
              {
                label: "Team Released Turnins",
                data: td.months.map((m) => (td.team_monthly_released || {})[m] || 0),
              },
            ],
            { perBarColor: true },
          );
    }

    return () => {
      destroyChart(commitsRef);
      destroyChart(linesRef);
      destroyChart(pieRef);
      destroyChart(monthRef);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tiData, subView]);

  if (gitLoading) {
    return <p style={{ color: "var(--dash-mute)" }}>Loading git metrics… (may take 30–60 s on cold cache)</p>;
  }
  if (gitError || !gitData) {
    const msg = gitErrorObj instanceof Error ? gitErrorObj.message : String(gitErrorObj ?? "Unknown error");
    return (
      <div style={{ color: "#f87171" }}>
        <p style={{ margin: 0 }}>Failed to load dashboard data.</p>
        <pre style={{ fontSize: "0.75rem", marginTop: "0.4rem", whiteSpace: "pre-wrap" }}>{msg}</pre>
      </div>
    );
  }

  const d = gitData as DashboardData;
  const t = d.team_totals;
  const topContributor = [...d.engineers].sort((a, b) => b.total - a.total)[0];

  return (
    <div>
      {/* KPI row */}
      <div className="dash-kpis">
        {[
          ["Project", d.project],
          ["Active Engineers", `${t.engineers} / ${d.engineers.length}`],
          ["Total Commits", t.total.toLocaleString()],
          ["Net Lines Changed", fmtNum(t.net_lines)],
          ["Top Contributor", topContributor ? `${topContributor.engineer.split(" ")[0]} (${topContributor.total})` : "—"],
          ["Window", d.window.label],
        ].map(([lbl, val]) => (
          <div key={lbl} className="dash-kpi">
            <div className="lbl">{lbl}</div>
            <div className="val" style={{ fontSize: 18 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Sub-toggle */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", margin: "12px 0 8px" }}>
        <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>View:</span>
        <div
          style={{
            display: "flex",
            gap: 4,
            background: "var(--dash-panel2)",
            border: "1px solid var(--dash-border2)",
            borderRadius: 10,
            padding: 4,
          }}
        >
          {(["commits", "turnins"] as const).map((v) => (
            <button
              key={v}
              className={`dash-tab${subView === v ? " is-active" : ""}`}
              onClick={() => setSubView(v)}
            >
              {v === "commits" ? "Commits" : "Turnins"}
            </button>
          ))}
        </div>
        {subView === "turnins" && tiLoading && (
          <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>Loading team turnins…</span>
        )}
      </div>

      {/* Charts row 1 */}
      <div className="dash-grid" style={{ marginTop: 8 }}>
        <div className="dash-card">
          <h3>{subView === "commits" ? "Commits per Engineer" : "Released Turnins per Engineer"}</h3>
          <div style={{ height: 300 }}>
            <canvas ref={canvasCommits} />
          </div>
        </div>
        <div className="dash-card">
          <h3>{subView === "commits" ? "Commit-Type Mix (Team)" : "Turnin Status Mix (Team)"}</h3>
          <div style={{ height: 300 }}>
            <canvas ref={canvasPie} />
          </div>
        </div>
      </div>

      {/* Charts row 2 */}
      <div className="dash-grid2">
        <div className="dash-card">
          <h3>{subView === "commits" ? "Net Lines Changed per Engineer" : "Files Touched per Engineer (released)"}</h3>
          <div style={{ height: 260 }}>
            <canvas ref={canvasLines} />
          </div>
        </div>
        <div className="dash-card">
          <h3>{subView === "commits" ? "Monthly Commit Distribution" : "Monthly Released Turnins"}</h3>
          <div style={{ height: 260 }}>
            <canvas ref={canvasMonth} />
          </div>
        </div>
      </div>

      {/* Leaderboard */}
      <div style={{ marginTop: 16 }}>
        <div className="dash-card">
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>
              {subView === "commits" ? "Team Leaderboard" : "Team Turnins Leaderboard"}
            </h3>
            <span style={{ color: "var(--dash-mute)", fontSize: 12 }}>
              {subView === "commits"
                ? "Click a row → Engineer Detail"
                : "Click a row → Turnins (TI)"}
            </span>
          </div>
          <div style={{ maxHeight: 400, overflowY: "auto" }}>
            {subView === "commits" ? (
              <table className="dash-tbl">
                <thead>
                  <tr>
                    <th>Engineer</th>
                    <th>IDSID</th>
                    <th>Commits</th>
                    <th>Net Lines</th>
                    <th>Avg</th>
                    <th>Median</th>
                    <th>% ≤ Med</th>
                    <th>Pattern</th>
                  </tr>
                </thead>
                <tbody>
                  {d.engineers.map((e) => (
                    <tr key={e.engineer} onClick={() => onSelectEngineer(e.engineer)}>
                      <td>{e.engineer}</td>
                      <td>
                        <code style={{ fontSize: 12 }}>{e.idsid || "–"}</code>
                      </td>
                      <td>{e.total}</td>
                      <td>{fmtNum(e.net_lines)}</td>
                      <td>{e.avg_lines}</td>
                      <td>{e.median_lines}</td>
                      <td>{e.pct_at_or_below}%</td>
                      <td>
                        <span className="dash-pill">{e.pattern || "–"}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              tiData && "engineers" in tiData ? (
                <TurninsLeaderboard
                  td={tiData as TeamTurninsReport}
                  onSelect={onSelectEngineerTI}
                />
              ) : (
                <p style={{ color: "var(--dash-mute)", padding: 8 }}>
                  {tiLoading ? "Loading…" : "No turnin data available."}
                </p>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function TurninsLeaderboard({
  td,
  onSelect,
}: {
  td: TeamTurninsReport;
  onSelect: (name: string) => void;
}) {
  const engs = [...td.engineers].sort((a, b) => (b.released || 0) - (a.released || 0));
  const projs = Object.keys(td.team_totals_by_project || {});
  const statusesUnion = Array.from(
    new Set(engs.flatMap((e) => Object.keys(e.status || {}))),
  ).sort();

  return (
    <table className="dash-tbl">
      <thead>
        <tr>
          <th>Engineer</th>
          <th>IDSID</th>
          <th>Released</th>
          <th>Total</th>
          <th>Files (rel.)</th>
          {projs.map((p) => (
            <th key={p}>{p} (rel/total)</th>
          ))}
          {statusesUnion.map((s) => (
            <th key={s}>{s}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {engs.map((e) => (
          <tr key={e.engineer} onClick={() => onSelect(e.engineer)}>
            <td>{e.engineer}</td>
            <td>
              <code style={{ fontSize: 12 }}>{e.idsid || "–"}</code>
            </td>
            <td>
              <b>{e.released || 0}</b>
            </td>
            <td style={{ color: "var(--dash-mute)" }}>{e.total}</td>
            <td>{e.released_files || 0}</td>
            {projs.map((p) => {
              const pp = e.per_project[p] || {};
              return (
                <td key={p}>
                  {pp.released || 0}{" "}
                  <span style={{ color: "var(--dash-mute)" }}>/ {pp.total || 0}</span>
                </td>
              );
            })}
            {statusesUnion.map((s) => (
              <td key={s}>{e.status[s] || 0}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
