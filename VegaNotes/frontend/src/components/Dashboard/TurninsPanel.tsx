/**
 * TurninsPanel.tsx — Shared turnin display: KPI chips, filter bar, and
 * expandable row table used by both TurninsTab (admin) and MyTIsTab (IC).
 */
import { useState } from "react";
import { TurninReport, TurninRecord } from "../../api/client";

interface Props {
  data: TurninReport;
}

function esc(s: unknown): string {
  return String(s == null ? "" : s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
}

function tiStatusBadge(status: string | null | undefined): JSX.Element {
  const s = String(status || "").trim();
  if (!s) return <span className="dash-pill">–</span>;
  const l = s.toLowerCase();
  let style: React.CSSProperties = {};
  if (/releas/.test(l))
    style = { background: "#0f4a2c", color: "#a7f3d0", borderColor: "#166534" };
  else if (/cancel|withdraw/.test(l))
    style = { background: "#5a3410", color: "#fed7aa", borderColor: "#9a3412" };
  else if (/reject|fail/.test(l))
    style = { background: "#5b1a1a", color: "#fecaca", borderColor: "#7f1d1d" };
  else if (/hold|pending|queue|progress|flight/.test(l))
    style = { background: "#1e3a5f", color: "#bfdbfe", borderColor: "#1e40af" };
  return (
    <span className="dash-pill" style={style}>
      {s}
    </span>
  );
}

function projectBadge(p: string): JSX.Element {
  const c = p === "GFC" ? "#22d3ee" : p === "JNC" ? "#f59e0b" : "#a5b4fc";
  return (
    <span
      className="dash-pill"
      style={{ background: c + "22", color: c, border: `1px solid ${c}55` }}
    >
      {p}
    </span>
  );
}

function crBadge(status: string | null | undefined, url: string | null | undefined): JSX.Element {
  if (!status) return <span>–</span>;
  let style: React.CSSProperties = {};
  if (/pass|approv|complete|released/i.test(status))
    style = { background: "#0f4a2c", color: "#a7f3d0" };
  else if (/fail|reject/i.test(status))
    style = { background: "#5b1a1a", color: "#fecaca" };
  else
    style = { background: "#3b3a1f", color: "#fde68a" };
  const badge = (
    <span className="dash-pill" style={style}>
      {status}
    </span>
  );
  return url ? (
    <a href={url} target="_blank" rel="noopener" style={{ textDecoration: "none" }}>
      {badge}
    </a>
  ) : (
    badge
  );
}

function hsdCell(hsds: string[]): JSX.Element {
  const list = (hsds || []).filter(Boolean);
  if (!list.length) return <span style={{ color: "var(--dash-mute)" }}>–</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, maxWidth: 220 }}>
      {list.map((h) => (
        <a
          key={h}
          href={`https://hsdes.intel.com/appstore/article/#/${encodeURIComponent(h)}`}
          target="_blank"
          rel="noopener"
          className="dash-pill"
          style={{
            background: "#3b2a5f",
            color: "#ddd6fe",
            borderColor: "#5b21b6",
            textDecoration: "none",
            fontFamily: "monospace",
          }}
        >
          {h}
        </a>
      ))}
    </div>
  );
}

function tiMatchesOne(t: TurninRecord, q: string): boolean {
  if (!q) return true;
  if (String(t.id || "").includes(q)) return true;
  if ((t.comments || "").toLowerCase().includes(q)) return true;
  if ((t.status || "").toLowerCase().includes(q)) return true;
  if ((t.stage || "").toLowerCase().includes(q)) return true;
  if ((t.cluster || "").toLowerCase().includes(q)) return true;
  if ((t.project || "").toLowerCase().includes(q)) return true;
  if ((t.code_review_status || "").toLowerCase().includes(q)) return true;
  for (const f of t.files_changed || []) if (f.toLowerCase().includes(q)) return true;
  for (const h of t.hsds_added || []) if (String(h).includes(q)) return true;
  for (const c of t.commits || [])
    if ((c.sha || "").startsWith(q) || (c.subject || "").toLowerCase().includes(q)) return true;
  return false;
}

function tiMatchesFilter(t: TurninRecord, rawQuery: string): boolean {
  if (!rawQuery) return true;
  const tokens = rawQuery
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  for (const tok of tokens) {
    const negate = tok.startsWith("-") || tok.startsWith("!");
    const needle = negate ? tok.slice(1).trim() : tok;
    if (!needle) continue;
    const hit = tiMatchesOne(t, needle);
    if (negate ? hit : !hit) return false;
  }
  return true;
}

export function TurninsPanel({ data }: Props) {
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const rows = data.turnins.filter((t) => tiMatchesFilter(t, filter));

  function toggleRow(idx: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  return (
    <div>
      {/* KPIs */}
      <div className="dash-kpis">
        {[
          ["Engineer", data.engineer + (data.idsid ? `  (${data.idsid})` : "")],
          ["Project", data.project],
          ["Window", data.window.label + "  (" + data.window.since + " → " + data.window.until + ")"],
          [
            "Turnins",
            Object.entries(data.totals || {})
              .map(([p, n]) => `${p}: ${n}`)
              .join("   •   ") || "0",
          ],
          ["Total", data.turnins.length.toString()],
          [
            "Released",
            data.turnins.filter((t) => /releas/i.test(t.status || "")).length.toString(),
          ],
          [
            "In-flight",
            data.turnins
              .filter((t) => /hold|pending|queue|progress|flight/i.test(t.status || ""))
              .length.toString(),
          ],
          [
            "Rejected",
            data.turnins
              .filter((t) => /reject|fail/i.test(t.status || ""))
              .length.toString(),
          ],
        ].map(([lbl, val]) => (
          <div key={lbl} className="dash-kpi">
            <div className="lbl">{lbl}</div>
            <div className="val" style={{ fontSize: 16 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Turnins table */}
      <div className="dash-card">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>Turnins</h3>
          <span className="dash-pill">
            {rows.length} turnin{rows.length === 1 ? "" : "s"}
          </span>
          <div style={{ flex: 1 }} />
          <input
            className="dash-input"
            placeholder="filter: comma-separated AND — e.g. released, pmh  (use -tok to exclude)"
            style={{ minWidth: 360 }}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div style={{ maxHeight: 640, overflowY: "auto" }}>
          <table className="dash-tbl">
            <thead>
              <tr>
                <th style={{ width: 26 }} />
                <th>Turnin</th>
                <th>Turnin Time</th>
                <th>Project</th>
                <th>Status</th>
                <th>Comment</th>
                <th># Commits</th>
                <th># Files</th>
                <th title="HSD IDs added to core/common/cfg/bugs">HSDs Added</th>
                <th>Code Review</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t, idx) => (
                <>
                  <tr key={`r-${idx}`} style={{ cursor: "default" }}>
                    <td>
                      <span
                        style={{ cursor: "pointer", userSelect: "none" }}
                        onClick={() => toggleRow(idx)}
                      >
                        {expanded.has(idx) ? "▾" : "▸"}
                      </span>
                    </td>
                    <td>
                      <code style={{ fontSize: 12 }}>TI {esc(t.id)}</code>
                      {t.bundle_id && (
                        <div style={{ color: "var(--dash-mute)", fontSize: 12 }}>
                          bundle {esc(t.bundle_id)}
                        </div>
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{t.turnin_time}</td>
                    <td>{projectBadge(t.project)}</td>
                    <td>
                      {tiStatusBadge(t.status)}
                      {t.stage && (
                        <div style={{ color: "var(--dash-mute)", fontSize: 12 }}>{t.stage}</div>
                      )}
                    </td>
                    <td
                      style={{
                        whiteSpace: "pre-wrap",
                        maxWidth: 520,
                        wordBreak: "break-word",
                        fontSize: 13,
                      }}
                    >
                      {t.comments || ""}
                    </td>
                    <td>{t.n_commits}</td>
                    <td>{(t.files_changed || []).length}</td>
                    <td>{hsdCell(t.hsds_added || [])}</td>
                    <td>{crBadge(t.code_review_status, t.code_review_url)}</td>
                  </tr>
                  {expanded.has(idx) && (
                    <tr key={`d-${idx}`}>
                      <td
                        colSpan={10}
                        style={{ background: "#0b1327", padding: 0 }}
                      >
                        <TurninDetail t={t} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={10} style={{ color: "var(--dash-mute)", textAlign: "center", padding: 16 }}>
                    No turnins match the current filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function TurninDetail({ t }: { t: TurninRecord }) {
  return (
    <div style={{ padding: "10px 14px" }}>
      {t.comments && (
        <div
          style={{
            color: "var(--dash-mute)",
            whiteSpace: "pre-wrap",
            marginBottom: 10,
            fontSize: 13,
          }}
        >
          <b>Comment:</b>
          {"\n"}
          {t.comments}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
        {/* Commits */}
        <div>
          <div style={{ color: "var(--dash-mute)", fontSize: 12, marginBottom: 4 }}>
            <b>Commits ({(t.commits || []).length})</b>
          </div>
          <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "3px 6px" }}>SHA</th>
                <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "3px 6px" }} />
                <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "3px 6px" }}>Subject</th>
                <th style={{ textAlign: "left", color: "var(--dash-mute)", padding: "3px 6px" }}>Date</th>
              </tr>
            </thead>
            <tbody>
              {(t.commits || []).length === 0 ? (
                <tr>
                  <td colSpan={4} style={{ color: "var(--dash-mute)", padding: "3px 6px" }}>
                    no commits parsed
                  </td>
                </tr>
              ) : (
                (t.commits || []).map((c, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12, padding: "3px 6px" }}>
                      {c.sha.slice(0, 12)}
                    </td>
                    <td style={{ padding: "3px 6px" }}>
                      {c.merge && (
                        <span
                          className="dash-pill"
                          style={{ background: "#3b3a1f", color: "#fde68a" }}
                        >
                          merge
                        </span>
                      )}
                    </td>
                    <td style={{ padding: "3px 6px" }}>{c.subject || ""}</td>
                    <td style={{ color: "var(--dash-mute)", fontSize: 12, padding: "3px 6px" }}>
                      {c.date || ""}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Files */}
        <div>
          <div style={{ color: "var(--dash-mute)", fontSize: 12, marginBottom: 4 }}>
            <b>Files ({(t.files_changed || []).length})</b>
          </div>
          {(t.files_changed || []).length === 0 ? (
            <span style={{ color: "var(--dash-mute)", fontSize: 13 }}>no files</span>
          ) : (
            <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
              <tbody>
                {(t.files_changed || []).map((f, fi) => (
                  <tr key={fi}>
                    <td
                      style={{
                        fontFamily: "ui-monospace, monospace",
                        fontSize: 12,
                        wordBreak: "break-all",
                        padding: "3px 6px",
                        color: "#93c5fd",
                      }}
                    >
                      {f}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
