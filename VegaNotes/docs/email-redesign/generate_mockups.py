#!/usr/bin/env python3
"""Generate 3 executive-summary email mockups for the Kanban 'Send Email' redesign.
Email-safe: table + inline-style CSS bars (survive clipboard->Outlook paste).
Line/trend shown two ways: robust column-chart + a graceful SVG line."""
import html, pathlib

OUT = pathlib.Path(__file__).parent

# ---- sample data (representative FIT Val Weekly WW31) -----------------------
PROJECT = "FIT Val Weekly"
WEEK = "WW31 · 2026-07-29"
STATUS = {"todo": 8, "in-progress": 6, "blocked": 2, "done": 14}
TOTAL = sum(STATUS.values())
DONE = STATUS["done"]
PCT = round(DONE / TOTAL * 100)
PRIOS = [("P0", 3, "#dc2626"), ("P1", 7, "#ea580c"), ("P2", 10, "#d97706"),
         ("P3", 6, "#0891b2"), ("P4", 4, "#64748b")]
OWNERS = [  # name, todo, ip, blocked, done
    ("njammala", 2, 3, 1, 5), ("gajith", 3, 1, 0, 4), ("sbhattad", 1, 1, 1, 2),
    ("abolisaw", 1, 0, 0, 2), ("mkasongo", 1, 1, 0, 1),
]
TREND = [("WW27", 8), ("WW28", 18), ("WW29", 30), ("WW30", 40), ("WW31", 47)]
TREND_DEN = 54
ATTENTION = [  # id, title, owner, why, eta
    ("T-800631", "JNC bucket debug", "njammala", "BLOCKED · P0", "WW30 (overdue)"),
    ("T-5W89KG", "Enable IDQ FV in DV", "njammala", "In-progress · P1", "WW32"),
    ("T-EY892H", "Share findings with Aya", "sbhattad", "BLOCKED · P1", "WW31"),
]
SC = {"todo": ("#e2e8f0", "#334155"), "in-progress": ("#2563eb", "#fff"),
      "blocked": ("#dc2626", "#fff"), "done": ("#16a34a", "#fff")}
SLAB = {"todo": "To-do", "in-progress": "In-progress", "blocked": "Blocked", "done": "Done"}

def esc(s): return html.escape(str(s))

# ---- reusable email-safe components -----------------------------------------
def kpi(label, value, sub="", accent="#0f172a", big=False):
    fs = "30px" if big else "24px"
    return f'''<td style="padding:0 6px;" width="20%">
      <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;background:#fff;border:1px solid #e2e8f0;border-top:3px solid {accent};border-radius:6px;">
        <tr><td style="padding:10px 12px;text-align:center;">
          <div style="font-size:{fs};font-weight:700;color:{accent};line-height:1;">{esc(value)}</div>
          <div style="font-size:11px;color:#64748b;margin-top:4px;text-transform:uppercase;letter-spacing:.4px;">{esc(label)}</div>
          {f'<div style="font-size:10px;color:#94a3b8;margin-top:2px;">{esc(sub)}</div>' if sub else ''}
        </td></tr>
      </table></td>'''

def kpi_row(tiles):
    return f'<table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;margin:0 0 16px;"><tr>{"".join(tiles)}</tr></table>'

def stacked_completion_bar():
    order = ["done", "in-progress", "todo", "blocked"]
    cells = ""
    for k in order:
        n = STATUS[k]
        if not n: continue
        w = round(n / TOTAL * 100)
        bg, fg = SC[k]
        cells += f'<td width="{w}%" style="background:{bg};color:{fg};font-size:11px;font-weight:600;text-align:center;padding:6px 0;white-space:nowrap;">{n}</td>'
    legend = " &nbsp; ".join(
        f'<span style="display:inline-block;width:9px;height:9px;background:{SC[k][0]};border-radius:2px;"></span> <span style="font-size:11px;color:#475569;">{SLAB[k]} {STATUS[k]}</span>'
        for k in order)
    return f'''<div style="font-size:12px;font-weight:700;color:#334155;letter-spacing:.4px;margin:0 0 6px;">STATUS DISTRIBUTION</div>
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;border-radius:6px;overflow:hidden;margin:0 0 6px;"><tr>{cells}</tr></table>
    <div style="margin:0 0 18px;">{legend}</div>'''

def hbar(label, value, maxv, color, width_px=360, suffix=""):
    w = round(value / maxv * 100) if maxv else 0
    return f'''<tr>
      <td style="font-size:12px;color:#334155;padding:3px 8px 3px 0;white-space:nowrap;" width="90">{esc(label)}</td>
      <td style="padding:3px 0;">
        <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;"><tr>
          <td width="{w}%" style="background:{color};height:14px;font-size:1px;line-height:1px;border-radius:3px;">&nbsp;</td>
          <td style="padding-left:8px;font-size:12px;color:#0f172a;font-weight:600;white-space:nowrap;">{value}{suffix}</td>
        </tr></table>
      </td></tr>'''

def priority_chart():
    mx = max(n for _, n, _ in PRIOS)
    rows = "".join(hbar(l, n, mx, c) for l, n, c in PRIOS)
    return f'''<div style="font-size:12px;font-weight:700;color:#334155;letter-spacing:.4px;margin:0 0 8px;">BY PRIORITY</div>
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;margin:0 0 18px;">{rows}</table>'''

def owner_stacked():
    rows = ""
    mx = max(sum(o[1:]) for o in OWNERS)
    for name, td, ip, bl, dn in sorted(OWNERS, key=lambda o: -sum(o[1:])):
        tot = td + ip + bl + dn
        segs = ""
        for k, n in (("done", dn), ("in-progress", ip), ("todo", td), ("blocked", bl)):
            if not n: continue
            w = round(n / mx * 100)
            segs += f'<td width="{w}%" style="background:{SC[k][0]};height:15px;font-size:1px;line-height:1px;">&nbsp;</td>'
        rows += f'''<tr>
          <td style="font-size:12px;color:#334155;padding:3px 8px 3px 0;white-space:nowrap;" width="80">{esc(name)}</td>
          <td style="padding:3px 0;"><table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;border-radius:3px;overflow:hidden;"><tr>{segs}<td style="padding-left:8px;font-size:12px;font-weight:600;color:#0f172a;white-space:nowrap;">{dn}/{tot}</td></tr></table></td>
        </tr>'''
    return f'''<div style="font-size:12px;font-weight:700;color:#334155;letter-spacing:.4px;margin:0 0 8px;">STATUS BY OWNER <span style="font-weight:400;color:#94a3b8;">(done / total)</span></div>
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;margin:0 0 18px;">{rows}</table>'''

def trend_columns():
    mx = TREND_DEN
    cols = ""
    for wk, v in TREND:
        h = round(v / mx * 80) + 4
        cols += f'''<td valign="bottom" align="center" style="padding:0 5px;">
          <div style="font-size:10px;color:#0f172a;font-weight:600;">{round(v/mx*100)}%</div>
          <div style="width:26px;height:{h}px;background:#2563eb;border-radius:3px 3px 0 0;"></div>
          <div style="font-size:10px;color:#64748b;margin-top:3px;">{wk}</div>
        </td>'''
    return f'''<div style="font-size:12px;font-weight:700;color:#334155;letter-spacing:.4px;margin:0 0 8px;">BUCKET-DEBUG PROGRESS <span style="font-weight:400;color:#94a3b8;">(fixed of {TREND_DEN}, weekly)</span></div>
    <table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:0 0 6px;"><tr>{cols}</tr></table>'''

def trend_svg():
    w, h, pad = 380, 96, 26
    xs = [pad + i * (w - 2 * pad) / (len(TREND) - 1) for i in range(len(TREND))]
    ys = [h - pad - (v / TREND_DEN) * (h - 2 * pad) for _, v in TREND]
    pts = " ".join(f"{x:.0f},{y:.0f}" for x, y in zip(xs, ys))
    dots = "".join(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3" fill="#2563eb"/>' for x, y in zip(xs, ys))
    labels = "".join(f'<text x="{x:.0f}" y="{h-1}" font-size="9" fill="#64748b" text-anchor="middle">{wk}</text>' for x, (wk, _) in zip(xs, TREND))
    area = f'{xs[0]:.0f},{h-pad} ' + pts + f' {xs[-1]:.0f},{h-pad}'
    return f'''<div style="font-size:12px;font-weight:700;color:#334155;letter-spacing:.4px;margin:0 0 6px;">BUCKET-DEBUG PROGRESS <span style="font-weight:400;color:#94a3b8;">(SVG line — modern clients)</span></div>
    <svg width="{w}" height="{h}" style="display:block;margin:0 0 18px;">
      <polyline points="{area}" fill="#dbeafe" stroke="none"/>
      <polyline points="{pts}" fill="none" stroke="#2563eb" stroke-width="2"/>{dots}{labels}
    </svg>'''

def attention_table():
    rows = ""
    for tid, title, owner, why, eta in ATTENTION:
        overdue = "overdue" in eta
        rows += f'''<tr>
          <td style="padding:6px 10px;font-size:12px;color:#0f172a;border-bottom:1px solid #f1f5f9;"><b>{esc(title)}</b><div style="font-size:10px;color:#94a3b8;">{esc(tid)} · @{esc(owner)}</div></td>
          <td style="padding:6px 10px;font-size:11px;border-bottom:1px solid #f1f5f9;white-space:nowrap;"><span style="color:{'#dc2626' if 'BLOCKED' in why else '#2563eb'};font-weight:600;">{esc(why)}</span></td>
          <td style="padding:6px 10px;font-size:11px;border-bottom:1px solid #f1f5f9;white-space:nowrap;color:{'#dc2626' if overdue else '#475569'};font-weight:{'700' if overdue else '400'};">{esc(eta)}</td>
        </tr>'''
    return f'''<div style="font-size:12px;font-weight:700;color:#b91c1c;letter-spacing:.4px;margin:0 0 8px;">⚠ NEEDS ATTENTION</div>
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;border:1px solid #fecaca;border-radius:6px;overflow:hidden;margin:0 0 18px;background:#fff;">
      <tr style="background:#fef2f2;"><td style="padding:5px 10px;font-size:10px;color:#991b1b;text-transform:uppercase;letter-spacing:.4px;">Task</td><td style="padding:5px 10px;font-size:10px;color:#991b1b;">Status</td><td style="padding:5px 10px;font-size:10px;color:#991b1b;">ETA</td></tr>
      {rows}</table>'''

def shell(title, tag, body):
    return f'''<!doctype html><html><body style="margin:0;background:#f1f5f9;padding:20px;">
    <div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0f172a;max-width:660px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">
      <div style="background:#0f172a;color:#fff;padding:16px 22px;">
        <div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;">{esc(tag)}</div>
        <div style="font-size:20px;font-weight:700;margin-top:2px;">{esc(PROJECT)} — Executive Summary</div>
        <div style="font-size:12px;color:#cbd5e1;margin-top:3px;">{esc(WEEK)} · {TOTAL} tasks · {PCT}% complete</div>
      </div>
      <div style="padding:20px 22px;">{body}</div>
      <div style="padding:12px 22px;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;">View live in VegaNotes · Sent from Kanban</div>
    </div></body></html>'''

# ---- Option 1: Executive Summary (KPI + completion bar + attention + owners)
opt1 = shell("opt1", "Option 1 · Executive Summary",
    kpi_row([
        kpi("Complete", f"{PCT}%", f"{DONE}/{TOTAL}", "#16a34a", big=True),
        kpi("In-progress", STATUS["in-progress"], "", "#2563eb"),
        kpi("Blocked", STATUS["blocked"], "", "#dc2626"),
        kpi("At-risk", 1, "overdue ETA", "#ea580c"),
        kpi("Owners", len(OWNERS), "active", "#0f172a"),
    ])
    + stacked_completion_bar()
    + attention_table()
    + owner_stacked())

# ---- Option 2: Analytical Dashboard (charts front & center) -----------------
opt2 = shell("opt2", "Option 2 · Analytical Dashboard",
    kpi_row([
        kpi("Total", TOTAL, "", "#0f172a"),
        kpi("Done", DONE, f"{PCT}%", "#16a34a"),
        kpi("Active", STATUS["in-progress"], "", "#2563eb"),
        kpi("Blocked", STATUS["blocked"], "", "#dc2626"),
        kpi("To-do", STATUS["todo"], "", "#64748b"),
    ])
    + trend_columns()
    + f'<div style="height:1px;background:#e2e8f0;margin:0 0 18px;"></div>'
    + priority_chart()
    + owner_stacked()
    + stacked_completion_bar())

# ---- Option 3: Hybrid (exec header + trend + retains task detail) -----------
sample_cards = ""
for tid, title, owner, why, eta in ATTENTION:
    sample_cards += f'''<table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:separate;background:#fff;border:1px solid #e2e8f0;border-left:4px solid #dc2626;border-radius:5px;margin:0 0 8px;"><tr><td style="padding:9px 12px;">
      <div style="font-size:14px;font-weight:600;">{esc(title)}</div>
      <div style="font-size:11px;color:#64748b;margin-top:3px;">{esc(tid)} · @{esc(owner)} · {esc(why)} · ETA {esc(eta)}</div>
    </td></tr></table>'''
opt3 = shell("opt3", "Option 3 · Hybrid (summary + detail)",
    kpi_row([
        kpi("Complete", f"{PCT}%", f"{DONE}/{TOTAL}", "#16a34a", big=True),
        kpi("In-progress", STATUS["in-progress"], "", "#2563eb"),
        kpi("Blocked", STATUS["blocked"], "", "#dc2626"),
        kpi("At-risk", 1, "overdue", "#ea580c"),
        kpi("Total", TOTAL, "", "#0f172a"),
    ])
    + stacked_completion_bar()
    + trend_svg()
    + f'<div style="font-size:12px;font-weight:700;color:#334155;letter-spacing:.4px;margin:0 0 8px;">BLOCKED · IN-PROGRESS (detail retained)</div>'
    + sample_cards
    + owner_stacked())

for name, content in (("option1.html", opt1), ("option2.html", opt2), ("option3.html", opt3)):
    (OUT / name).write_text(content, encoding="utf-8")
    print("wrote", name)
