import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect(r"C:/Users/Garrison/career/monitor.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Session stats
cur.execute("""
    SELECT agent_name,
           COUNT(*) as total,
           SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) as complete,
           SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
           SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) as partial,
           ROUND(AVG(duration_seconds),1) as avg_duration
    FROM session_log GROUP BY agent_name
""")
session_stats = [dict(r) for r in cur.fetchall()]

cur.execute("SELECT COUNT(*) as total FROM session_log")
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
    SELECT date, emails_processed, tier1_count, tier2_count,
           tier3_count, auto_filtered, duration_seconds
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

data = {
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

with open(r"C:/Users/Garrison/career/monitor-data.js", "w") as f:
    f.write(f"const MONITOR_DATA = {json.dumps(data, indent=2)};")

print("monitor-data.js written.")
