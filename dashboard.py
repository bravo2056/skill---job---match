#!/usr/bin/env python3
"""Job Match Analytics Dashboard Generator
Run: python3 dashboard.py
Output: dashboard.html (open in browser)
"""

import sqlite3, json, re, os
from datetime import datetime, timedelta
from collections import defaultdict, Counter

DB  = r"C:/Users/Garrison/career/job-tracker.db"
OUT = r"C:/Users/Garrison/career/dashboard.html"

APPLY_TARGET = 3   # minimum applications per week


# ── Data helpers ──────────────────────────────────────────────────────────────

def load_rows():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM reviewed_postings ORDER BY reviewed_at ASC, id ASC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def week_start(date_str):
    if not date_str:
        return None
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            d = datetime.strptime(s[:10], fmt)
            return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def fmt_week(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%b %d").replace(" 0", " ")   # "Feb  9" -> "Feb 9"
    except Exception:
        return date_str


def parse_comp_floor(comp):
    """Return lowest dollar figure from a comp string as a K integer."""
    if not comp:
        return None
    m = re.search(r"\$?(\d+)(?:\.\d+)?/hr", comp, re.I)
    if m:
        return round(int(float(m.group(1))) * 2080 / 1000)
    nums = [int(x) for x in re.findall(r"\$?(\d{2,3})[Kk]", comp)]
    return min(nums) if nums else None


def parse_source(link):
    """Map a link/text to a job source label."""
    if not link:
        return "No Link / Unknown"
    l = str(link).lower()
    if "jobright"    in l: return "Jobright"
    if "linkedin"    in l: return "LinkedIn"
    if "hiringcafe"  in l: return "HiringCafe"
    if "indeed"      in l: return "Indeed"
    if "ziprecruiter" in l or "zip recruiter" in l: return "ZipRecruiter"
    if any(x in l for x in ("email", "recruiter", "recruiting")): return "Recruiter / Email"
    # Anything else is a direct company or niche board link
    return "Direct / Company Site"


def categorize_pass(verdict):
    """Return (macro_category, domain_subcategory) for a passed role."""
    if not verdict:
        return ("Hard Req / Other", "")
    v = verdict.lower()

    # ── Domain detection (check before role-type so domain + location rows
    #    are bucketed under Domain Mismatch, not Location)
    domain = None
    if any(x in v for x in ("fintech", "financial service", "banking",
                             "finance", "payments", "fintech/banking",
                             "fintech/financial")):
        domain = "Finance / Fintech"
    elif any(x in v for x in ("healthcare", "health domain", "health ",
                               "pharma", "biotech", "biomedical", "hipaa",
                               "clinical", "medical")):
        domain = "Healthcare / Biotech"
    elif any(x in v for x in ("data center", "hyperscale", "hardware",
                               "firmware", "embedded")):
        domain = "Hardware / Data Center"
    elif any(x in v for x in ("government", "federal", "govcon",
                               "aerospace", "construction")):
        domain = "Gov / Aerospace / Construction"
    elif any(x in v for x in ("advertising", "ad agency", "media agency")):
        domain = "Advertising / Media"
    elif any(x in v for x in ("consumer tech", "consumer ai", "consumer mobile",
                               "consumer hardware", "consumer product")):
        domain = "Consumer Tech"
    elif "cybersecurity" in v or ("cyber" in v and "domain" in v):
        domain = "Cybersecurity"
    elif any(x in v for x in ("real estate", "mortgage", "m&a integration",
                               "publishing", "edtech", "mes ", "logistics",
                               "retail", "food industry", "industrial controls",
                               "manufacturing", "ip admin", "pet ")):
        domain = "Other Domain"
    elif "domain" in v:
        domain = "Other Domain"

    if domain:
        return ("Domain Mismatch", domain)

    # ── Wrong role type
    if any(x in v for x in ("not tpm", "not pm", "not a tech", "not a pm",
                             "software engineer", "design role", "analyst role",
                             "data analyst", "coordinator", "architect role",
                             "specialist not", "developer relations", "evangelist",
                             "vp/gm", "pm role, not", "solutions architect",
                             "qa auditor", "business architect")):
        return ("Wrong Role Type", "")

    # ── Comp under floor (check before location — some verdicts mention both)
    if "comp" in v and any(x in v for x in ("floor", "under $", "ceiling", "$56k",
                                              "$61k", "$68k", "$80k", "$84k",
                                              "$48k", "$50k", "$70k", "$90k")):
        return ("Comp Under Floor", "")

    # ── Location / commute / travel
    if any(x in v for x in ("onsite", "commute", "travel", "location",
                             "arizona", "bay area", "philadelphia", "ohio")):
        return ("Location / Commute", "")

    # ── Underleveled
    if any(x in v for x in ("underleveled", "under-leveled", "coordinator level",
                             "associate level", "analyst level")):
        return ("Underleveled", "")

    return ("Hard Req / Other", "")


# ── Monitor data loader ───────────────────────────────────────────────────────

def load_monitor_data():
    """Load agent monitor data from monitor.db. Returns empty structure if DB missing."""
    MONITOR_DB = r"C:/Users/Garrison/career/monitor.db"
    empty = {
        "generated_at": "",
        "kpis": {"total_sessions": 0, "completion_rate": 0, "total_flags": 0, "avg_duration": 0},
        "session_stats": [], "event_dist": [], "flag_dist": [],
        "scan_history": [], "recent_sessions": [], "recent_flags": []
    }
    if not os.path.exists(MONITOR_DB):
        return empty
    try:
        conn = sqlite3.connect(MONITOR_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT agent_name,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) as complete,
                   SUM(CASE WHEN status='failed'   THEN 1 ELSE 0 END) as failed,
                   SUM(CASE WHEN status='partial'  THEN 1 ELSE 0 END) as partial,
                   ROUND(AVG(duration_seconds),1) as avg_duration
            FROM session_log GROUP BY agent_name
        """)
        session_stats = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT COUNT(*) FROM scan_metrics")
        total_sessions = cur.fetchone()[0]

        cur.execute("""
            SELECT ROUND(100.0 * SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) / COUNT(*), 1)
            FROM session_log
        """)
        completion_rate = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM quality_flags")
        total_flags = cur.fetchone()[0]

        cur.execute("SELECT ROUND(AVG(duration_seconds),1) FROM session_log WHERE duration_seconds IS NOT NULL")
        avg_duration = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT event_type, COUNT(*) as count
            FROM event_log GROUP BY event_type ORDER BY count DESC
        """)
        event_dist = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT flag_type, severity, COUNT(*) as count
            FROM quality_flags GROUP BY flag_type, severity ORDER BY count DESC
        """)
        flag_dist = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT date, emails_processed, roles_extracted, pending_written,
                   duplicates_skipped, rejected, duration_seconds
            FROM scan_metrics ORDER BY date ASC
        """)
        scan_history = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT session_id, agent_name, start_time, duration_seconds, status
            FROM session_log ORDER BY start_time DESC LIMIT 20
        """)
        recent_sessions = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT timestamp, agent_name, flag_type, severity, description
            FROM quality_flags ORDER BY timestamp DESC LIMIT 20
        """)
        recent_flags = [dict(r) for r in cur.fetchall()]

        conn.close()

        return {
            "generated_at": datetime.now().isoformat(),
            "kpis": {
                "total_sessions": total_sessions,
                "completion_rate": completion_rate,
                "total_flags": total_flags,
                "avg_duration": avg_duration
            },
            "session_stats": session_stats,
            "event_dist": event_dist,
            "flag_dist": flag_dist,
            "scan_history": scan_history,
            "recent_sessions": recent_sessions,
            "recent_flags": recent_flags
        }
    except Exception:
        return empty


# ── Compute all sections ──────────────────────────────────────────────────────

def all_weeks_between(start_str, end_str):
    """Yield all Monday-week-start strings between two dates (inclusive)."""
    d = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    while d <= end:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(weeks=1)


def compute(rows):
    total_reviewed = len(rows)

    TERMINAL    = ("Pass", "Closed")
    PIPELINE    = ("Reviewed", "Queued", "Applied", "Screening", "Interview", "Offer")
    IN_PROGRESS = ("Applied", "Screening", "Interview", "Offer")

    active_rows      = [r for r in rows if r["status"] not in TERMINAL]
    pending_rows     = [r for r in rows if r["status"] == "Pending"]
    pipeline_rows    = [r for r in rows if r["status"] in PIPELINE]
    in_progress_rows = [r for r in rows if r["status"] in IN_PROGRESS]
    applied          = [r for r in rows if r["status"] == "Applied"]
    passed           = [r for r in rows if r["status"] == "Pass"]
    closed           = [r for r in rows if r["status"] == "Closed"]
    tier1            = [r for r in active_rows if (r["score_pct"] or 0) >= 65]

    # ── Funnel
    funnel = {
        "stages": ["Total Reviewed", "Active Pipeline", "Tier 1 Active", "Applied"],
        "values": [total_reviewed, len(pipeline_rows), len(tier1), len(applied)],
    }

    # ── Score distribution
    buckets = defaultdict(int)
    for r in rows:
        p = r["score_pct"]
        if p is not None:
            buckets[(p // 10) * 10] += 1
    score_dist = {
        "labels": [f"{i*10}-{i*10+9}%" for i in range(10)],
        "values": [buckets.get(i * 10, 0) for i in range(10)],
    }

    # ── Status breakdown
    status_counts = Counter(r["status"] for r in rows)
    status_order  = ["Pending", "Reviewed", "Queued", "Applied", "Screening", "Interview", "Offer", "Closed", "Pass"]
    qual_labels   = [s for s in status_order if s in status_counts]
    quality = {
        "labels": qual_labels,
        "values": [status_counts[s] for s in qual_labels],
    }

    # ── Pass reason frequency — two-tier
    pass_cats = [categorize_pass(r.get("notes"))
                 for r in passed]
    macro_ctr  = Counter(c[0] for c in pass_cats)
    domain_ctr = Counter(c[1] for c in pass_cats if c[1])

    macro_order  = ["Domain Mismatch", "Wrong Role Type", "Comp Under Floor",
                    "Location / Commute", "Underleveled", "Hard Req / Other"]
    macro_labels = [x for x in macro_order if x in macro_ctr]
    macro_vals   = [macro_ctr[x] for x in macro_labels]

    domain_sorted = sorted(domain_ctr.items(), key=lambda x: x[1], reverse=True)
    pass_freq = {
        "macro":  {"labels": macro_labels,                       "values": macro_vals},
        "domain": {"labels": [x[0] for x in domain_sorted],     "values": [x[1] for x in domain_sorted]},
    }

    # ── Weekly applied velocity (all weeks from first review to today)
    now_str    = datetime.now().strftime("%Y-%m-%d")
    first_week = week_start(rows[0]["reviewed_at"]) if rows else now_str
    today_week = week_start(now_str)
    # fallback if any date parse fails
    if not first_week: first_week = now_str
    if not today_week: today_week = now_str

    applied_by_week = defaultdict(int)
    for r in applied:
        w = week_start(r["reviewed_at"])
        if w:
            applied_by_week[w] += 1

    all_w = list(all_weeks_between(first_week, today_week))
    velocity = {
        "labels":  [fmt_week(w) for w in all_w],
        "values":  [applied_by_week.get(w, 0) for w in all_w],
        "target":  APPLY_TARGET,
    }

    # ── Pipeline tables (split by state)
    def make_table_rows(source_rows, date_field="reviewed_at"):
        source_rows = sorted(source_rows, key=lambda r: (r["score_pct"] or 0), reverse=True)
        return [{
            "company": r["company"],
            "role":    r["role"],
            "score":   r["score_pct"],
            "status":  r["status"],
            "comp":    r["comp"] or "-",
            "remote":  "Remote" if r["remote"] == 1 else (r["remote"] or "-"),
            "date":    (r[date_field][:10] if r.get(date_field) else "-"),
            "link":    r["link"] or "",
        } for r in source_rows]

    in_progress_table = make_table_rows(in_progress_rows, "applied_date")
    pending_table     = make_table_rows(pending_rows, "reviewed_at")

    # ── Comp coverage
    with_comp    = [r for r in rows if r["comp"] and r["comp"].strip()]
    without_comp = total_reviewed - len(with_comp)
    floors = [f for f in (parse_comp_floor(r["comp"]) for r in with_comp) if f]

    comp_buckets = {"<130K": 0, "130-150K": 0, "150-175K": 0, "175-200K": 0, "200K+": 0}
    for f in floors:
        if   f < 130: comp_buckets["<130K"]    += 1
        elif f < 150: comp_buckets["130-150K"] += 1
        elif f < 175: comp_buckets["150-175K"] += 1
        elif f < 200: comp_buckets["175-200K"] += 1
        else:         comp_buckets["200K+"]    += 1

    comp = {
        "coverage": {"labels": ["Comp Posted", "Not Posted"],
                     "values": [len(with_comp), without_comp]},
        "dist":     {"labels": list(comp_buckets.keys()),
                     "values": list(comp_buckets.values())},
    }

    # ── Job sources
    source_ctr    = Counter(parse_source(r.get("link")) for r in rows)
    sources_sorted = sorted(source_ctr.items(), key=lambda x: x[1], reverse=True)
    sources = {
        "labels": [x[0] for x in sources_sorted],
        "values": [x[1] for x in sources_sorted],
    }

    # ── Summary KPIs
    active_scored = [r["score_pct"] for r in active_rows if r["score_pct"] is not None]
    avg_score = round(sum(active_scored) / len(active_scored), 1) if active_scored else 0

    active_denominator = len(active_rows)

    summary = {
        "total":       total_reviewed,
        "applied":     len(applied),
        "closed":      len(closed),
        "active":      len(active_rows),
        "in_progress": len(in_progress_rows),
        "pending":     len(pending_rows),
        "passed":      len(passed),
        "avg_score":   avg_score,
        "apply_rate":  round(len(applied) / active_denominator * 100, 1) if active_denominator else 0,
        "generated":   datetime.now().strftime("%B %d, %Y  -  %I:%M %p"),
    }

    return summary, funnel, score_dist, quality, pass_freq, velocity, in_progress_table, pending_table, comp, sources


# ── HTML helpers ──────────────────────────────────────────────────────────────

def status_badge(s):
    # Dark-mode-friendly badge colors
    colors = {
        "Applied":    ("#064e3b", "#6ee7b7"),
        "Pending":    ("#1e3a5f", "#93c5fd"),
        "Pass":       ("#7f1d1d", "#fca5a5"),
        "Closed":     ("#1c1917", "#78716c"),
        "Reviewed":   ("#1f2937", "#9ca3af"),
        "Queued":     ("#78350f", "#fcd34d"),
        "Screening":  ("#1e3a5f", "#93c5fd"),
        "Interview":  ("#1e3a5f", "#93c5fd"),
        "Offer":      ("#064e3b", "#6ee7b7"),
    }
    bg, fg = colors.get(s, ("#1f2937", "#9ca3af"))
    return (f'<span style="background:{bg};color:{fg};padding:2px 9px;'
            f'border-radius:9999px;font-size:11px;font-weight:600;'
            f'letter-spacing:.4px;">{s}</span>')


def score_color(pct):
    if pct is None: return "#6b7280"
    if pct >= 65:   return "#34d399"
    if pct >= 50:   return "#fbbf24"
    return "#f87171"


def pipeline_rows_html(pipeline):
    out = []
    for i, r in enumerate(pipeline):
        bg = "#1e293b" if i % 2 == 0 else "#243044"
        sc = score_color(r["score"])
        score_cell = (f'<span style="color:{sc};font-weight:700;">{r["score"]}%</span>'
                      if r["score"] is not None
                      else '<span style="color:#4b5563;">-</span>')
        role_cell = (f'<a href="{r["link"]}" target="_blank" '
                     f'style="color:#818cf8;text-decoration:none;">{r["role"]}</a>'
                     if r["link"] else r["role"])
        out.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:8px 14px;color:#e2e8f0;">{r["company"]}</td>'
            f'<td style="padding:8px 14px;">{role_cell}</td>'
            f'<td style="padding:8px 14px;text-align:center;">{score_cell}</td>'
            f'<td style="padding:8px 14px;text-align:center;">{status_badge(r["status"])}</td>'
            f'<td style="padding:8px 14px;font-size:12px;color:#94a3b8;">{r["comp"]}</td>'
            f'<td style="padding:8px 14px;font-size:12px;color:#94a3b8;">{r["remote"]}</td>'
            f'<td style="padding:8px 14px;font-size:12px;color:#64748b;">{r.get("date") or "-"}</td>'
            f'</tr>'
        )
    return "\n".join(out)


def funnel_html_blocks(funnel):
    out = []
    max_val = funnel["values"][0] or 1
    stage_colors = ["#6366f1", "#8b5cf6", "#34d399", "#fbbf24"]
    for i, (stage, val) in enumerate(zip(funnel["stages"], funnel["values"])):
        width_pct = val / max_val * 100
        rate = (f"{val / funnel['values'][i-1] * 100:.0f}% of prev"
                if i > 0 else "")
        rate_html = (f'<span style="font-size:11px;color:#64748b;margin-left:8px;">'
                     f'({rate})</span>') if rate else ""
        out.append(
            f'<div style="margin:6px 0;display:flex;align-items:center;">'
            f'  <div style="width:{width_pct:.1f}%;min-width:90px;'
            f'background:{stage_colors[i]};color:#fff;padding:11px 0;'
            f'border-radius:5px;text-align:center;font-weight:700;font-size:15px;'
            f'transition:width .3s;">{val}</div>'
            f'  <span style="margin-left:12px;font-size:13px;color:#cbd5e1;">'
            f'    {stage}{rate_html}'
            f'  </span>'
            f'</div>'
        )
    return "\n".join(out)


# ── HTML generation ───────────────────────────────────────────────────────────

def generate_html(summary, funnel, score_dist, quality, pass_freq,
                  velocity, in_progress_table, pending_table, comp, sources, monitor_data=None):
    j = lambda d: json.dumps(d)

    in_progress_rows_html = pipeline_rows_html(in_progress_table)
    pending_rows_html_str = pipeline_rows_html(pending_table)
    monitor_json = json.dumps(monitor_data or {})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Match Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<script>const MONITOR_DATA = {monitor_json};</script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
  }}

  /* ── Top bar ── */
  .topbar {{
    background: #020617;
    padding: 14px 28px;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid #1e293b;
    position: sticky; top: 0; z-index: 10;
  }}
  .topbar h1 {{ font-size: 17px; font-weight: 700; letter-spacing: -.3px; color: #f1f5f9; }}
  .topbar .meta {{ font-size: 12px; color: #475569; display: flex; align-items: center; gap: 14px; }}
  .refresh-btn {{
    background: #4f46e5; color: #fff; border: none; border-radius: 6px;
    padding: 7px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: background .15s;
  }}
  .refresh-btn:hover {{ background: #6366f1; }}

  /* ── Layout ── */
  .main {{ max-width: 1440px; margin: 0 auto; padding: 24px 28px; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 20px; }}
  .row-2-1  {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 20px; }}
  .row-1-1  {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}
  .row-full {{ margin-bottom: 20px; }}
  .row-1-2  {{ display: grid; grid-template-columns: 1fr 2fr; gap: 16px; margin-bottom: 20px; }}

  /* ── Card ── */
  .card {{
    background: #1e293b;
    border: 1px solid #263449;
    border-radius: 10px;
    padding: 22px 24px;
  }}
  .card h2 {{
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .8px; color: #64748b; margin-bottom: 18px;
  }}
  .chart-wrap {{ position: relative; }}

  /* ── KPI cards ── */
  .kpi {{
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 20px 22px;
    border-top: 3px solid;
    text-align: center;
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-height: 132px;
  }}
  .kpi .lbl {{
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .8px;
    color: #94a3b8;
    margin-bottom: 12px;
  }}
  .kpi .val {{
    font-size: 36px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 8px;
  }}
  .kpi .sub {{
    font-size: 12px;
    color: #cbd5e1;
    line-height: 1.35;
  }}

  /* ── Table ── */
  table {{ width: 100%; border-collapse: collapse; }}
  th {{
    text-align: left; font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .6px; color: #475569; padding: 8px 14px;
    border-bottom: 1px solid #263449;
  }}
  td {{ border-bottom: 1px solid #1a2840; font-size: 13px; }}
  tr:last-child td {{ border-bottom: none; }}
  a:hover {{ text-decoration: underline !important; }}

  @media (max-width: 960px) {{
    .kpi-row, .row-2-1, .row-1-1, .row-1-2 {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>

<div class="topbar">
  <div style="display:flex;align-items:center;gap:24px;">
    <h1>Job Match Analytics</h1>
    <nav style="display:flex;gap:4px;">
      <button id="tab-jobs" onclick="switchTab('jobs')" style="background:#4f46e5;color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer;">Job Match</button>
      <button id="tab-agents" onclick="switchTab('agents')" style="background:#1e293b;color:#94a3b8;border:1px solid #263449;border-radius:6px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer;">Agents</button>
    </nav>
  </div>
  <div class="meta">
    <span style="display:flex;align-items:center;gap:6px;">
      <span style="width:7px;height:7px;border-radius:50%;background:#34d399;
                   box-shadow:0 0 6px #34d399;display:inline-block;"></span>
      Live &mdash; {summary["generated"]}
    </span>
    <button class="refresh-btn" onclick="window.location.href='/?t='+Date.now()">&#8635; Refresh</button>
  </div>
</div>

<div id="view-jobs">
<div class="main">

  <!-- KPI row -->
  <div class="kpi-row">
    <div class="kpi" style="border-color:#60a5fa;">
      <div class="lbl">Total Reviewed</div>
      <div class="val" style="color:#60a5fa;">{summary["total"]}</div>
      <div class="sub">All jobs reviewed and parsed</div>
    </div>
    <div class="kpi" style="border-color:#34d399;">
      <div class="lbl">Applied</div>
      <div class="val" style="color:#34d399;">{summary["applied"]}</div>
      <div class="sub">{summary["applied"]} applied · {summary["closed"]} closed · {summary["apply_rate"]}% of active</div>
    </div>
    <div class="kpi" style="border-color:#f59e0b;">
      <div class="lbl">Active Pipeline</div>
      <div class="val" style="color:#f59e0b;">{summary["active"]}</div>
      <div class="sub">{summary["in_progress"]} in progress · {summary["pending"]} pending review</div>
    </div>
    <div class="kpi" style="border-color:#60a5fa;">
      <div class="lbl">Avg Match Score</div>
      <div class="val" style="color:#60a5fa;">{summary["avg_score"]}%</div>
      <div class="sub">Average score across active jobs</div>
    </div>
  </div>

  <!-- Sources + Quality -->
  <div class="row-2-1">
    <div class="card">
      <h2>Job Sources</h2>
      <div class="chart-wrap" style="height:230px;">
        <canvas id="sourcesChart"></canvas>
      </div>
    </div>
    <div class="card">
      <h2>Status Breakdown</h2>
      <div class="chart-wrap" style="height:230px;">
        <canvas id="qualChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Score Dist + Pass Reason -->
  <div class="row-1-1">
    <div class="card">
      <h2>Score Distribution</h2>
      <div class="chart-wrap" style="height:250px;">
        <canvas id="scoreChart"></canvas>
      </div>
    </div>
    <div class="card">
      <h2>Pass Reason Frequency</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;height:250px;">
        <div class="chart-wrap"><canvas id="passMacroChart"></canvas></div>
        <div class="chart-wrap"><canvas id="passDomainChart"></canvas></div>
      </div>
      <div style="display:flex;gap:20px;margin-top:10px;">
        <span style="font-size:11px;color:#475569;">LEFT: macro category</span>
        <span style="font-size:11px;color:#475569;">RIGHT: domain breakdown</span>
      </div>
    </div>
  </div>

  <!-- Weekly Applied Velocity -->
  <div class="row-full">
    <div class="card">
      <h2>Weekly Applied Velocity
        <span style="font-weight:400;color:#334155;margin-left:8px;">
          -- -- target: {APPLY_TARGET}/wk
        </span>
      </h2>
      <div class="chart-wrap" style="height:190px;">
        <canvas id="velChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Comp Coverage -->
  <div class="row-1-2">
    <div class="card">
      <h2>Comp Coverage</h2>
      <div class="chart-wrap" style="height:210px;">
        <canvas id="compCovChart"></canvas>
      </div>
    </div>
    <div class="card">
      <h2>Comp Floor Distribution</h2>
      <div class="chart-wrap" style="height:210px;">
        <canvas id="compDistChart"></canvas>
      </div>
    </div>
  </div>

  <div class="row-1-1">
    <div class="card">
      <h2>Applied / In Progress &mdash; {summary["in_progress"]} roles</h2>
      <table>
        <thead>
          <tr>
            <th>Company</th><th>Role</th>
            <th style="text-align:center;">Score</th>
            <th style="text-align:center;">Status</th>
            <th>Comp</th><th>Remote</th><th>Applied</th>
          </tr>
        </thead>
        <tbody>
          {in_progress_rows_html}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h2>Pending Review &mdash; {summary["pending"]} roles</h2>
      <table>
        <thead>
          <tr>
            <th>Company</th><th>Role</th>
            <th style="text-align:center;">Score</th>
            <th style="text-align:center;">Status</th>
            <th>Comp</th><th>Remote</th><th>Reviewed</th>
          </tr>
        </thead>
        <tbody>
          {pending_rows_html_str}
        </tbody>
      </table>
    </div>
  </div>

</div><!-- /main -->
</div><!-- /view-jobs -->

<div id="view-agents" style="display:none;">
  <div class="main">

    <!-- KPI row -->
    <div class="kpi-row">
      <div class="kpi" style="border-color:#6366f1;">
        <div class="lbl">Total Sessions</div>
        <div class="val" id="ag-total-sessions" style="color:#818cf8;">--</div>
        <div class="sub">All agents</div>
      </div>
      <div class="kpi" style="border-color:#34d399;">
        <div class="lbl">Completion Rate</div>
        <div class="val" id="ag-completion-rate" style="color:#34d399;">--</div>
        <div class="sub">Task success %</div>
      </div>
      <div class="kpi" style="border-color:#f87171;">
        <div class="lbl">Quality Flags</div>
        <div class="val" id="ag-total-flags" style="color:#f87171;">--</div>
        <div class="sub">Total incidents</div>
      </div>
      <div class="kpi" style="border-color:#fbbf24;">
        <div class="lbl">Avg Duration</div>
        <div class="val" id="ag-avg-duration" style="color:#fbbf24;">--</div>
        <div class="sub">Seconds per session</div>
      </div>
    </div>

    <!-- Session status + Event distribution -->
    <div class="row-2-1">
      <div class="card">
        <h2>Session Status by Agent</h2>
        <div class="chart-wrap" style="height:230px;">
          <canvas id="agSessionChart"></canvas>
        </div>
      </div>
      <div class="card">
        <h2>Event Type Distribution</h2>
        <div class="chart-wrap" style="height:230px;">
          <canvas id="agEventChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Quality flags + Scan performance -->
    <div class="row-1-1">
      <div class="card">
        <h2>Quality Flags by Type</h2>
        <div class="chart-wrap" style="height:250px;">
          <canvas id="agFlagChart"></canvas>
        </div>
      </div>
      <div class="card">
        <h2>Scan Output Over Time</h2>
        <div class="chart-wrap" style="height:250px;">
          <canvas id="agScanChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Recent sessions table -->
    <div class="card row-full">
      <h2>Recent Sessions</h2>
      <table>
        <thead>
          <tr>
            <th>Session ID</th><th>Agent</th><th>Start Time</th>
            <th style="text-align:center;">Duration (sec)</th>
            <th style="text-align:center;">Status</th>
          </tr>
        </thead>
        <tbody id="ag-sessions-tbody"></tbody>
      </table>
    </div>

    <!-- Recent flags table -->
    <div class="card row-full">
      <h2>Recent Quality Flags</h2>
      <table>
        <thead>
          <tr>
            <th>Timestamp</th><th>Agent</th><th>Flag Type</th>
            <th style="text-align:center;">Severity</th><th>Description</th>
          </tr>
        </thead>
        <tbody id="ag-flags-tbody"></tbody>
      </table>
    </div>

  </div>
</div><!-- /view-agents -->

<script>
// ── Global dark-mode defaults ──────────────────────────────────────────────
Chart.defaults.color          = '#64748b';
Chart.defaults.borderColor    = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family    = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.font.size      = 12;

const C = (id) => document.getElementById(id).getContext("2d");

const GRID  = 'rgba(255,255,255,0.06)';
const TICK  = '#475569';

// ── Job Sources — horizontal bar ──────────────────────────────────────────
new Chart(C("sourcesChart"), {{
  type: "bar",
  data: {{
    labels: {j(sources["labels"])},
    datasets: [{{
      data: {j(sources["values"])},
      backgroundColor: ["#6366f1","#818cf8","#a78bfa","#c4b5fd","#34d399","#fbbf24","#94a3b8"],
      borderRadius: 4
    }}]
  }},
  options: {{
    indexAxis: "y",
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend:{{ display:false }} }},
    scales: {{
      x: {{ beginAtZero:true, ticks:{{ stepSize:5, color:TICK }}, grid:{{ color:GRID }} }},
      y: {{ grid:{{ display:false }}, ticks:{{ color:"#94a3b8", font:{{ size:12 }} }} }}
    }}
  }}
}});

// ── Quality Ratio — donut ──────────────────────────────────────────────────
new Chart(C("qualChart"), {{
  type: "doughnut",
  data: {{
    labels: {j(quality["labels"])},
    datasets: [{{
      data: {j(quality["values"])},
      backgroundColor: ["#34d399","#fbbf24","#60a5fa","#6b7280","#f87171"],
      borderColor: "#1e293b", borderWidth: 3
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position:"right", labels:{{ color:"#94a3b8", boxWidth:12, padding:14 }} }}
    }}
  }}
}});

// ── Score Distribution — bar ───────────────────────────────────────────────
new Chart(C("scoreChart"), {{
  type: "bar",
  data: {{
    labels: {j(score_dist["labels"])},
    datasets: [{{
      data: {j(score_dist["values"])},
      backgroundColor: [
        "#f87171","#f87171","#f87171","#f87171","#f87171",
        "#fbbf24","#fbbf24",
        "#34d399","#34d399","#34d399"
      ],
      borderRadius: 4, borderSkipped: false
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend:{{ display:false }} }},
    scales: {{
      x: {{ grid:{{ display:false }}, ticks:{{ color:TICK }} }},
      y: {{ beginAtZero:true, ticks:{{ stepSize:1, color:TICK }}, grid:{{ color:GRID }} }}
    }}
  }}
}});

// ── Pass Reason — Macro (left) ─────────────────────────────────────────────
new Chart(C("passMacroChart"), {{
  type: "bar",
  data: {{
    labels: {j(pass_freq["macro"]["labels"])},
    datasets: [{{
      data: {j(pass_freq["macro"]["values"])},
      backgroundColor: ["#818cf8","#f87171","#fbbf24","#fb923c","#a78bfa","#94a3b8"],
      borderRadius: 4
    }}]
  }},
  options: {{
    indexAxis: "y",
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend:{{ display:false }} }},
    scales: {{
      x: {{ beginAtZero:true, ticks:{{ stepSize:1, color:TICK }}, grid:{{ color:GRID }} }},
      y: {{ grid:{{ display:false }}, ticks:{{ color:TICK, font:{{ size:11 }} }} }}
    }}
  }}
}});

// ── Pass Reason — Domain breakdown (right) ─────────────────────────────────
new Chart(C("passDomainChart"), {{
  type: "bar",
  data: {{
    labels: {j(pass_freq["domain"]["labels"])},
    datasets: [{{
      data: {j(pass_freq["domain"]["values"])},
      backgroundColor: "#334155",
      hoverBackgroundColor: "#818cf8",
      borderRadius: 4
    }}]
  }},
  options: {{
    indexAxis: "y",
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend:{{ display:false }} }},
    scales: {{
      x: {{ beginAtZero:true, ticks:{{ stepSize:1, color:TICK }}, grid:{{ color:GRID }} }},
      y: {{ grid:{{ display:false }}, ticks:{{ color:TICK, font:{{ size:10 }} }} }}
    }}
  }}
}});

// ── Weekly Applied Velocity — bar + target line ────────────────────────────
new Chart(C("velChart"), {{
  type: "bar",
  data: {{
    labels: {j(velocity["labels"])},
    datasets: [
      {{
        label: "Applications",
        data: {j(velocity["values"])},
        backgroundColor: "#4f46e5",
        hoverBackgroundColor: "#818cf8",
        borderRadius: 4,
        order: 2
      }},
      {{
        label: "Target ({APPLY_TARGET}/wk)",
        data: Array({len(velocity["values"])}).fill({APPLY_TARGET}),
        type: "line",
        borderColor: "#f59e0b",
        borderDash: [6, 4],
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
        order: 1
      }}
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{
        display: true,
        labels: {{ color:"#64748b", boxWidth:24, padding:16 }}
      }}
    }},
    scales: {{
      x: {{ grid:{{ display:false }}, ticks:{{ color:TICK }} }},
      y: {{
        beginAtZero: true,
        suggestedMax: {APPLY_TARGET + 2},
        ticks:{{ stepSize:1, color:TICK }},
        grid:{{ color:GRID }}
      }}
    }}
  }}
}});

// ── Comp Coverage — donut ──────────────────────────────────────────────────
new Chart(C("compCovChart"), {{
  type: "doughnut",
  data: {{
    labels: {j(comp["coverage"]["labels"])},
    datasets: [{{
      data: {j(comp["coverage"]["values"])},
      backgroundColor: ["#34d399","#1e3a5f"],
      borderColor: "#1e293b", borderWidth: 3
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position:"bottom", labels:{{ color:"#94a3b8", padding:14 }} }}
    }}
  }}
}});

// ── Comp Floor Distribution — bar ──────────────────────────────────────────
new Chart(C("compDistChart"), {{
  type: "bar",
  data: {{
    labels: {j(comp["dist"]["labels"])},
    datasets: [{{
      data: {j(comp["dist"]["values"])},
      backgroundColor: ["#f87171","#fbbf24","#34d399","#34d399","#818cf8"],
      borderRadius: 4
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend:{{ display:false }} }},
    scales: {{
      x: {{ grid:{{ display:false }}, ticks:{{ color:TICK }} }},
      y: {{ beginAtZero:true, ticks:{{ stepSize:1, color:TICK }}, grid:{{ color:GRID }} }}
    }}
  }}
}});

// ── Tab switcher ─────────────────────────────────────────────────────────
function switchTab(tab) {{
  document.getElementById('view-jobs').style.display    = tab === 'jobs'   ? '' : 'none';
  document.getElementById('view-agents').style.display = tab === 'agents' ? '' : 'none';
  document.getElementById('tab-jobs').style.background    = tab === 'jobs'   ? '#4f46e5' : '#1e293b';
  document.getElementById('tab-jobs').style.color         = tab === 'jobs'   ? '#fff'    : '#94a3b8';
  document.getElementById('tab-jobs').style.border        = tab === 'jobs'   ? 'none'    : '1px solid #263449';
  document.getElementById('tab-agents').style.background  = tab === 'agents' ? '#4f46e5' : '#1e293b';
  document.getElementById('tab-agents').style.color       = tab === 'agents' ? '#fff'    : '#94a3b8';
  document.getElementById('tab-agents').style.border      = tab === 'agents' ? 'none'    : '1px solid #263449';
}}

// ── Agent monitor data ───────────────────────────────────────────────────
(function() {{
  if (typeof MONITOR_DATA === 'undefined') return;
  const d = MONITOR_DATA;

  document.getElementById('ag-total-sessions').textContent  = d.kpis.total_sessions  ?? '--';
  document.getElementById('ag-completion-rate').textContent = (d.kpis.completion_rate ?? '--') + '%';
  document.getElementById('ag-total-flags').textContent     = d.kpis.total_flags      ?? '--';
  document.getElementById('ag-avg-duration').textContent    = d.kpis.avg_duration     ?? '--';

  const AGRID = 'rgba(255,255,255,0.06)';
  const ATICK = '#475569';
  const AC = (id) => document.getElementById(id).getContext('2d');

  // Session status by agent
  const agents   = d.session_stats.map(r => r.agent_name);
  const complete = d.session_stats.map(r => r.complete || 0);
  const failed   = d.session_stats.map(r => r.failed   || 0);
  const partial  = d.session_stats.map(r => r.partial  || 0);
  new Chart(AC('agSessionChart'), {{
    type: 'bar',
    data: {{
      labels: agents.length ? agents : ['No data'],
      datasets: [
        {{ label:'Complete', data: complete, backgroundColor:'#34d399', borderRadius:4 }},
        {{ label:'Failed',   data: failed,   backgroundColor:'#f87171', borderRadius:4 }},
        {{ label:'Partial',  data: partial,  backgroundColor:'#fbbf24', borderRadius:4 }}
      ]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      plugins:{{ legend:{{ labels:{{ color:'#94a3b8' }} }} }},
      scales:{{
        x:{{ stacked:true, grid:{{ display:false }}, ticks:{{ color:ATICK }} }},
        y:{{ stacked:true, beginAtZero:true, ticks:{{ stepSize:1, color:ATICK }}, grid:{{ color:AGRID }} }}
      }}
    }}
  }});

  // Event type distribution
  new Chart(AC('agEventChart'), {{
    type: 'bar',
    data: {{
      labels: d.event_dist.length ? d.event_dist.map(r => r.event_type) : ['No data'],
      datasets:[{{ data: d.event_dist.length ? d.event_dist.map(r => r.count) : [0],
        backgroundColor:'#6366f1', borderRadius:4 }}]
    }},
    options:{{
      indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{{ legend:{{ display:false }} }},
      scales:{{
        x:{{ beginAtZero:true, ticks:{{ color:ATICK }}, grid:{{ color:AGRID }} }},
        y:{{ grid:{{ display:false }}, ticks:{{ color:'#94a3b8' }} }}
      }}
    }}
  }});

  // Quality flags by type
  const flagColors = {{ high:'#f87171', medium:'#fbbf24', low:'#34d399' }};
  new Chart(AC('agFlagChart'), {{
    type: 'bar',
    data: {{
      labels: d.flag_dist.length ? d.flag_dist.map(r => r.flag_type) : ['No data'],
      datasets:[{{ data: d.flag_dist.length ? d.flag_dist.map(r => r.count) : [0],
        backgroundColor: d.flag_dist.length ? d.flag_dist.map(r => flagColors[r.severity] || '#64748b') : ['#64748b'],
        borderRadius:4 }}]
    }},
    options:{{
      indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{{ legend:{{ display:false }} }},
      scales:{{
        x:{{ beginAtZero:true, ticks:{{ color:ATICK }}, grid:{{ color:AGRID }} }},
        y:{{ grid:{{ display:false }}, ticks:{{ color:'#94a3b8' }} }}
      }}
    }}
  }});

  // Scan output over time
  new Chart(AC('agScanChart'), {{
    type: 'line',
    data: {{
      labels: d.scan_history.length ? d.scan_history.map(r => r.date) : ['No data'],
      datasets:[
        {{ label:'Emails Processed', data: d.scan_history.map(r => r.emails_processed || 0),
          borderColor:'#6366f1', backgroundColor:'rgba(99,102,241,0.1)', tension:.3, fill:true, pointRadius:4 }},
        {{ label:'Roles Extracted', data: d.scan_history.map(r => r.roles_extracted || 0), borderColor:'#34d399', tension:.3, pointRadius:4 }},
        {{ label:'Pending Written', data: d.scan_history.map(r => r.pending_written || 0), borderColor:'#fbbf24', tension:.3, pointRadius:4 }},
        {{ label:'Duplicates Skipped', data: d.scan_history.map(r => r.duplicates_skipped || 0), borderColor:'#94a3b8', tension:.3, pointRadius:4 }},
        {{ label:'Rejected', data: d.scan_history.map(r => r.rejected || 0), borderColor:'#f87171', tension:.3, pointRadius:4 }}
      ]
    }},
    options:{{
      responsive:true, maintainAspectRatio:false,
      plugins:{{ legend:{{ labels:{{ color:'#94a3b8' }} }} }},
      scales:{{
        x:{{ grid:{{ display:false }}, ticks:{{ color:ATICK }} }},
        y:{{ beginAtZero:true, ticks:{{ color:ATICK }}, grid:{{ color:AGRID }} }}
      }}
    }}
  }});

  // Recent sessions table
  const sTbody = document.getElementById('ag-sessions-tbody');
  if (d.recent_sessions.length) {{
    const statusBadge = s => {{
      const map = {{ complete:['#064e3b','#6ee7b7'], failed:['#450a0a','#fca5a5'], partial:['#78350f','#fcd34d'] }};
      const [bg,fg] = map[s] || ['#1e293b','#94a3b8'];
      return `<span style="background:${{bg}};color:${{fg}};padding:2px 9px;border-radius:9999px;font-size:11px;font-weight:600;">${{s}}</span>`;
    }};
    sTbody.innerHTML = d.recent_sessions.map((r,i) => `
      <tr style="background:${{i%2?'#243044':'#1e293b'}};">
        <td style="padding:8px 14px;font-size:12px;color:#64748b;">${{r.session_id}}</td>
        <td style="padding:8px 14px;">${{r.agent_name}}</td>
        <td style="padding:8px 14px;font-size:12px;color:#94a3b8;">${{r.start_time}}</td>
        <td style="padding:8px 14px;text-align:center;font-size:12px;color:#94a3b8;">${{r.duration_seconds ?? '--'}}</td>
        <td style="padding:8px 14px;text-align:center;">${{statusBadge(r.status)}}</td>
      </tr>`).join('');
  }} else {{
    sTbody.innerHTML = '<tr><td colspan="5" style="padding:16px 14px;color:#475569;text-align:center;">No sessions logged yet.</td></tr>';
  }}

  // Recent flags table
  const fTbody = document.getElementById('ag-flags-tbody');
  if (d.recent_flags.length) {{
    const sevBadge = s => {{
      const map = {{ high:['#450a0a','#fca5a5'], medium:['#78350f','#fcd34d'], low:['#064e3b','#6ee7b7'] }};
      const [bg,fg] = map[s] || ['#1e293b','#94a3b8'];
      return `<span style="background:${{bg}};color:${{fg}};padding:2px 9px;border-radius:9999px;font-size:11px;font-weight:600;">${{s}}</span>`;
    }};
    fTbody.innerHTML = d.recent_flags.map((r,i) => `
      <tr style="background:${{i%2?'#243044':'#1e293b'}};">
        <td style="padding:8px 14px;font-size:12px;color:#64748b;">${{r.timestamp}}</td>
        <td style="padding:8px 14px;">${{r.agent_name}}</td>
        <td style="padding:8px 14px;">${{r.flag_type}}</td>
        <td style="padding:8px 14px;text-align:center;">${{sevBadge(r.severity)}}</td>
        <td style="padding:8px 14px;font-size:12px;color:#94a3b8;">${{r.description ?? ''}}</td>
      </tr>`).join('');
  }} else {{
    fTbody.innerHTML = '<tr><td colspan="5" style="padding:16px 14px;color:#475569;text-align:center;">No flags logged yet.</td></tr>';
  }}

}})();
</script>
</body>
</html>"""


# ── Test suite ────────────────────────────────────────────────────────────────

def run_tests():
    """Run a suite of sanity checks against the DB and computed data.
    Returns a list of dicts: {name, status, detail}
    where status is one of "PASS", "FAIL", "WARN".
    """
    results = []

    def ok(name, detail=""):
        results.append({"name": name, "status": "PASS", "detail": detail})

    def fail(name, detail=""):
        results.append({"name": name, "status": "FAIL", "detail": detail})

    def warn(name, detail=""):
        results.append({"name": name, "status": "WARN", "detail": detail})

    # ── DB Health ──────────────────────────────────────────────────────────────
    if not os.path.exists(DB):
        fail("DB Health - File Exists", f"DB not found at {DB}")
        # Can't continue without a DB
        return results
    else:
        ok("DB Health - File Exists", f"{DB}")

    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reviewed_postings")
        row_count = cur.fetchone()[0]
        ok("DB Health - Table Readable", f"reviewed_postings accessible")
    except Exception as e:
        fail("DB Health - Table Readable", str(e))
        return results

    if row_count > 0:
        ok("DB Health - Row Count", f"{row_count} rows")
    else:
        fail("DB Health - Row Count", "Table is empty (0 rows)")

    # ── Schema ─────────────────────────────────────────────────────────────────
    expected_cols = {"id", "applied_date", "company", "role", "score_pct", "status",
                     "comp", "link", "notes"}
    try:
        cur.execute("PRAGMA table_info(reviewed_postings)")
        actual_cols = {row["name"] for row in cur.fetchall()}
        missing = expected_cols - actual_cols
        if missing:
            fail("Schema - Expected Columns", f"Missing: {sorted(missing)}")
        else:
            ok("Schema - Expected Columns", f"All {len(expected_cols)} expected columns present")
    except Exception as e:
        fail("Schema - Expected Columns", str(e))

    # Load rows for remaining tests
    try:
        cur.execute("SELECT * FROM reviewed_postings ORDER BY reviewed_at ASC, id ASC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        fail("DB Load - All Rows", str(e))
        return results

    # ── Data Integrity - Applied ───────────────────────────────────────────────
    applied = [r for r in rows if r.get("status") == "Applied"]
    if len(applied) > 0:
        ok("Data Integrity - Applied Count", f"{len(applied)} applied rows")
    else:
        fail("Data Integrity - Applied Count", "No Applied rows found")

    missing_fields = [r for r in applied if not r.get("company") or not r.get("role")]
    if missing_fields:
        fail("Data Integrity - Applied Has Company+Role",
             f"{len(missing_fields)} applied row(s) missing company or role")
    else:
        ok("Data Integrity - Applied Has Company+Role",
           f"All {len(applied)} applied rows have company + role")

    # ── Data Integrity - Status values ─────────────────────────────────────────
    known_statuses = {"Pending", "Reviewed", "Queued", "Applied", "Screening", "Interview", "Offer", "Pass", "Closed"}
    bad_statuses = [r["status"] for r in rows if r.get("status") not in known_statuses]
    if bad_statuses:
        from collections import Counter as _C
        fail("Data Integrity - Status Values",
             f"{len(bad_statuses)} unknown status(es): {dict(_C(bad_statuses))}")
    else:
        ok("Data Integrity - Status Values",
           f"All statuses in known set {sorted(known_statuses)}")

    # ── Data Integrity - Score range ───────────────────────────────────────────
    out_of_range = [r for r in rows
                    if r.get("score_pct") is not None
                    and not (0 <= r["score_pct"] <= 100)]
    if out_of_range:
        fail("Data Integrity - Score Range",
             f"{len(out_of_range)} row(s) with score_pct outside 0-100")
    else:
        ok("Data Integrity - Score Range",
           f"All non-null scores in 0-100 range")

    # ── Data Integrity - Dates ─────────────────────────────────────────────────
    bad_dates = [r for r in rows if week_start(r.get("reviewed_at")) is None]
    if bad_dates:
        warn("Data Integrity - Dates",
             f"{len(bad_dates)} row(s) with unparseable date values")
    else:
        ok("Data Integrity - Dates",
           f"All {len(rows)} row dates parse successfully")

    # ── Compute checks ─────────────────────────────────────────────────────────
    try:
        t0 = datetime.now()
        summary, funnel, score_dist, quality, pass_freq, velocity, in_progress_table, pending_table, comp, sources = compute(rows)
        elapsed = (datetime.now() - t0).total_seconds()
    except Exception as e:
        fail("Compute - General", str(e))
        return results

    # KPIs
    if summary["total"] > 0:
        ok("Compute - KPIs Total", f"total={summary['total']}")
    else:
        fail("Compute - KPIs Total", "total == 0")

    if 0 <= summary["avg_score"] <= 100:
        ok("Compute - KPIs Avg Score", f"avg_score={summary['avg_score']}")
    else:
        fail("Compute - KPIs Avg Score", f"avg_score={summary['avg_score']} out of 0-100")

    if 0 <= summary["apply_rate"] <= 100:
        ok("Compute - KPIs Apply Rate", f"apply_rate={summary['apply_rate']}%")
    else:
        fail("Compute - KPIs Apply Rate", f"apply_rate={summary['apply_rate']} out of 0-100")

    # Score distribution
    if len(score_dist["labels"]) == 10:
        scored_rows = [r for r in rows if r.get("score_pct") is not None]
        dist_total = sum(score_dist["values"])
        if dist_total == len(scored_rows):
            ok("Compute - Score Distribution",
               f"10 buckets, total={dist_total} matches {len(scored_rows)} scored rows")
        else:
            fail("Compute - Score Distribution",
                 f"Bucket total {dist_total} != scored rows {len(scored_rows)}")
    else:
        fail("Compute - Score Distribution",
             f"Expected 10 buckets, got {len(score_dist['labels'])}")

    # Pass reasons
    if pass_freq["macro"]["labels"]:
        ok("Compute - Pass Reasons",
           f"{len(pass_freq['macro']['labels'])} macro label(s)")
    else:
        fail("Compute - Pass Reasons", "macro labels list is empty")

    # Velocity
    if velocity["all_w"] if "all_w" in velocity else velocity.get("labels"):
        all_w_vals = velocity["values"]
        if all(v >= 0 for v in all_w_vals):
            ok("Compute - Velocity",
               f"{len(all_w_vals)} weeks, all values >= 0")
        else:
            fail("Compute - Velocity", "Some weekly values are negative")
    else:
        fail("Compute - Velocity", "all_w / labels list is empty")

    # Sources
    if sources["labels"]:
        ok("Compute - Sources", f"{len(sources['labels'])} source label(s)")
    else:
        fail("Compute - Sources", "labels list is empty")

    # ── Comp Coverage ──────────────────────────────────────────────────────────
    with_comp = [r for r in rows if r.get("comp") and str(r["comp"]).strip()]
    if with_comp:
        ok("Comp Coverage", f"{len(with_comp)} role(s) have comp data")
    else:
        fail("Comp Coverage", "No roles have comp data")

    # ── Active Pipeline ────────────────────────────────────────────────────────
    PIPELINE = ("Reviewed", "Queued", "Applied", "Screening", "Interview", "Offer")
    active_rows = [r for r in rows if r.get("status") in PIPELINE]
    if active_rows:
        ok("Active Pipeline", f"{len(active_rows)} active pipeline row(s)")
    else:
        fail("Active Pipeline", "no active pipeline rows found")

    # ── Server Freshness ───────────────────────────────────────────────────────
    try:
        gen_dt = datetime.strptime(summary["generated"], "%B %d, %Y  -  %I:%M %p")
        # Replace year since strptime won't know the year; use current year
        gen_dt = gen_dt.replace(year=datetime.now().year)
        age_s = abs((datetime.now() - gen_dt).total_seconds())
        if age_s <= 10:
            ok("Server Freshness", f"Generated {age_s:.1f}s ago")
        else:
            warn("Server Freshness", f"Generated {age_s:.1f}s ago (expected <= 10s)")
    except Exception as e:
        warn("Server Freshness", f"Could not parse generated timestamp: {e}")

    return results


def tests_html(results):
    """Render a dark-themed HTML test report page."""
    passed  = sum(1 for r in results if r["status"] == "PASS")
    failed  = sum(1 for r in results if r["status"] == "FAIL")
    warned  = sum(1 for r in results if r["status"] == "WARN")
    ts      = datetime.now().strftime("%B %d, %Y  -  %I:%M %p")

    badge_styles = {
        "PASS": ("background:#064e3b;color:#6ee7b7;", "PASS"),
        "FAIL": ("background:#7f1d1d;color:#fca5a5;", "FAIL"),
        "WARN": ("background:#78350f;color:#fcd34d;", "WARN"),
    }

    rows_html = []
    for r in results:
        bstyle, blabel = badge_styles.get(r["status"], ("", r["status"]))
        badge = (f'<span style="{bstyle}padding:3px 10px;border-radius:9999px;'
                 f'font-size:11px;font-weight:700;letter-spacing:.4px;">{blabel}</span>')
        rows_html.append(
            f'<tr>'
            f'<td style="padding:10px 16px;color:#e2e8f0;font-size:13px;">{r["name"]}</td>'
            f'<td style="padding:10px 16px;text-align:center;">{badge}</td>'
            f'<td style="padding:10px 16px;color:#94a3b8;font-size:12px;">{r["detail"]}</td>'
            f'</tr>'
        )

    summary_bar_color = "#7f1d1d" if failed else ("#78350f" if warned else "#064e3b")
    summary_text_color = "#fca5a5" if failed else ("#fcd34d" if warned else "#6ee7b7")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard Tests</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f172a; color: #e2e8f0; min-height: 100vh;
  }}
  .topbar {{
    background: #020617; padding: 14px 28px;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid #1e293b;
    position: sticky; top: 0; z-index: 10;
  }}
  .topbar h1 {{ font-size: 17px; font-weight: 700; color: #f1f5f9; }}
  .topbar .meta {{ font-size: 12px; color: #475569; display: flex; align-items: center; gap: 14px; }}
  .refresh-btn {{
    background: #4f46e5; color: #fff; border: none; border-radius: 6px;
    padding: 7px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: background .15s;
  }}
  .refresh-btn:hover {{ background: #6366f1; }}
  .back-link {{
    color: #818cf8; text-decoration: none; font-size: 13px; font-weight: 600;
  }}
  .back-link:hover {{ text-decoration: underline; }}
  .main {{ max-width: 960px; margin: 0 auto; padding: 28px; }}
  .summary-bar {{
    background: {summary_bar_color}; color: {summary_text_color};
    padding: 14px 20px; border-radius: 8px; margin-bottom: 20px;
    font-size: 15px; font-weight: 700; letter-spacing: .2px;
  }}
  .card {{
    background: #1e293b; border: 1px solid #263449; border-radius: 10px;
    overflow: hidden;
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{
    text-align: left; font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .6px; color: #475569; padding: 10px 16px;
    border-bottom: 1px solid #263449;
  }}
  tr:nth-child(even) {{ background: #243044; }}
  td {{ border-bottom: 1px solid #1a2840; }}
  tr:last-child td {{ border-bottom: none; }}
  .ts {{ font-size: 12px; color: #475569; margin-top: 14px; }}
</style>
</head>
<body>
<div class="topbar">
  <h1>Dashboard Tests</h1>
  <div class="meta">
    <a href="/" class="back-link">&#8592; Dashboard</a>
    <button class="refresh-btn" onclick="window.location.href='/tests?t='+Date.now()">&#8635; Refresh</button>
  </div>
</div>
<div class="main">
  <div class="summary-bar">
    {passed} passed &nbsp;&bull;&nbsp; {failed} failed &nbsp;&bull;&nbsp; {warned} warnings
    &nbsp;&mdash;&nbsp; {len(results)} tests total
  </div>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Test</th>
          <th style="text-align:center;width:80px;">Status</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows_html)}
      </tbody>
    </table>
  </div>
  <div class="ts">Tests ran at {ts}</div>
</div>
</body>
</html>"""


# ── Server ────────────────────────────────────────────────────────────────────

def serve(host="localhost", port=5500):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import traceback, webbrowser, threading

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"SERA_MARKER_0410")

        def log_message(self, fmt, *args):
            # Print a compact single-line access log
            print(f"  [{self.log_date_time_string()}] {args[0]}")

    def _heartbeat():
        while True:
            threading.Event().wait(600)   # 10 minutes
            try:
                conn = sqlite3.connect(DB)
                cur  = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM reviewed_postings")
                total = cur.fetchone()[0]
                conn.close()
            except Exception:
                total = "?"
            print(f"[HEARTBEAT] Dashboard alive — "
                  f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — "
                  f"{total} roles in DB")

    url = f"http://{host}:{port}"
    print(f"Dashboard live -> {url}")
    print("Ctrl+C to stop.\n")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    hb = threading.Thread(target=_heartbeat, daemon=True, name="heartbeat")
    hb.start()

    HTTPServer((host, port), Handler).serve_forever()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--file" in sys.argv:
        # One-shot file write (legacy)
        rows = load_rows()
        data = compute(rows)
        monitor_data = load_monitor_data()
        html = generate_html(*data, monitor_data=monitor_data)
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Dashboard written -> {OUT}")
    else:
        serve()
