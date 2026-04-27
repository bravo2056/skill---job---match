#!/usr/bin/env python3
"""
SERA Integrity Sidecar
Sole gatekeeper for job-tracker.db writes.
Usage: python integrity.py --action <action> --payload '<json>'
"""

import sqlite3, re, json, argparse
from datetime import datetime, timezone

DB_PATH = r"C:/Users/Garrison/career/job-tracker.db"

VALID_STATUSES  = {'Pending','Reviewed','Queued','Applied','Screening','Interview','Offer','Pass','Closed'}
APPLIED_STATES  = {'Applied','Screening','Interview','Offer','Closed'}
TERMINAL_STATES = {'Pass','Closed'}

ACRONYM_MAP = {
    'sr': 'senior',
    'jr': 'junior',
    'tpm': 'technical program manager',
    'pm': 'program manager',
    'po': 'product owner',
    'mgr': 'manager',
    'dir': 'director',
    'eng': 'engineer',
    'ops': 'operations',
    'tech': 'technology',
    'coord': 'coordinator',
    'spec': 'specialist',
}


def company_key(raw):
    if not raw: return ''
    return re.sub(r'\s+', ' ', re.sub(r'[.,\-&]', '', raw.lower().strip()))

def role_key(raw):
    if not raw: return ''
    key = raw.lower().strip()
    key = key.replace('&', 'and')
    key = re.sub(r'[^\w\s]', '', key)
    tokens = key.split()
    expanded = []
    for t in tokens:
        if t in ACRONYM_MAP:
            expanded.extend(ACRONYM_MAP[t].split())
        else:
            expanded.append(t)
    tokens = sorted(set(expanded))
    return ' '.join(tokens)

def now_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

def reject(msg):
    return {"result": "REJECTED", "data": {}, "message": msg}

def valid_method(code, cur):
    cur.execute("SELECT 1 FROM applied_methods WHERE code=? AND active=1", (code,))
    return cur.fetchone() is not None


def handle_insert(payload, conn):
    cur = conn.cursor()

    for field in ('company', 'role', 'status'):
        if not payload.get(field):
            return reject(f"Missing required field: {field}")

    status = payload['status']
    if status not in VALID_STATUSES:
        return reject(f"Invalid status: '{status}'. Must be one of: {sorted(VALID_STATUSES)}")

    if status in APPLIED_STATES:
        if not payload.get('applied_date'):
            return reject("applied_date is required for Applied+ status")
        if not payload.get('applied_method'):
            return reject("applied_method is required for Applied+ status")
        if not valid_method(payload['applied_method'], cur):
            return reject(f"applied_method '{payload['applied_method']}' not in applied_methods table.")

    ck = company_key(payload['company'])
    rk = role_key(payload['role'])
    cur.execute(
        "SELECT id, company, role, status FROM reviewed_postings WHERE company_key=? AND role_key=?",
        (ck, rk)
    )
    existing = cur.fetchone()
    if existing:
        return {
            "result": "DUPLICATE",
            "data": {"existing_id": existing[0], "company": existing[1],
                     "role": existing[2], "status": existing[3]},
            "message": f"Already exists: id={existing[0]}, status={existing[3]}"
        }

    ts = now_utc()
    applied_at = ts if status in APPLIED_STATES  else None
    closed_at  = ts if status in TERMINAL_STATES else None

    cur.execute("""
        INSERT INTO reviewed_postings (
            company, company_key, role, role_key,
            applied_date, applied_method, status,
            reviewed_at, applied_at, closed_at, updated_at,
            score_pct, comp, remote, link, source,
            track, full_jd_reviewed, staffing_agency, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        payload['company'], ck, payload['role'], rk,
        payload.get('applied_date'), payload.get('applied_method'), status,
        ts, applied_at, closed_at, ts,
        payload.get('score_pct'), payload.get('comp'),
        payload.get('remote') or payload.get('remote_status', ''), payload.get('link'), payload.get('source'),
        payload.get('track'), payload.get('full_jd_reviewed', 0),
        payload.get('staffing_agency'), payload.get('notes')
    ))
    conn.commit()
    new_id = cur.lastrowid
    _touch_write_marker(conn)
    return {"result": "APPROVED", "data": {"id": new_id}, "message": f"Inserted (id={new_id})"}


def handle_update_status(payload, conn):
    cur = conn.cursor()

    if not payload.get('id') or not payload.get('status'):
        return reject("id and status required for update_status")

    status = payload['status']
    if status not in VALID_STATUSES:
        return reject(f"Invalid status: '{status}'")

    cur.execute(
        "SELECT status, applied_date, applied_method FROM reviewed_postings WHERE id=?",
        (payload['id'],)
    )
    row = cur.fetchone()
    if not row:
        return reject(f"No record with id={payload['id']}")

    current_status, existing_date, existing_method = row

    if status in APPLIED_STATES:
        resolved_date   = payload.get('applied_date')   or existing_date
        resolved_method = payload.get('applied_method') or existing_method
        if not resolved_date:
            return reject("applied_date required when transitioning to Applied+ status")
        if not resolved_method:
            return reject("applied_method required when transitioning to Applied+ status")
        if not valid_method(resolved_method, cur):
            return reject(f"applied_method '{resolved_method}' not in applied_methods table")

    ts = now_utc()
    sets = ["status=?", "updated_at=?"]
    vals = [status, ts]

    if payload.get('applied_date'):
        sets.append("applied_date=?"); vals.append(payload['applied_date'])
    if payload.get('applied_method'):
        sets.append("applied_method=?"); vals.append(payload['applied_method'])
    if payload.get('notes') is not None:
        sets.append("notes=?"); vals.append(payload['notes'])
    if payload.get('remote_status') is not None:
        sets.append("remote=?"); vals.append(payload['remote_status'])
    if status in APPLIED_STATES and current_status not in APPLIED_STATES:
        sets.append("applied_at=?"); vals.append(ts)
    if status in TERMINAL_STATES:
        sets.append("closed_at=?"); vals.append(ts)

    vals.append(payload['id'])
    cur.execute(f"UPDATE reviewed_postings SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    _touch_write_marker(conn)
    return {"result": "OK", "data": {"id": payload['id']}, "message": f"Status updated to {status}"}


def handle_update_score(payload, conn):
    cur = conn.cursor()

    if not payload.get('id'):
        return reject("id is required for update_score")
    if payload.get('score_pct') is None:
        return reject("score_pct is required for update_score")

    score_pct = payload['score_pct']
    if not isinstance(score_pct, int) or not (0 <= score_pct <= 100):
        return reject(f"score_pct must be an integer 0-100, got: {score_pct}")

    cur.execute(
        "SELECT id, status FROM reviewed_postings WHERE id=?",
        (payload['id'],)
    )
    row = cur.fetchone()
    if not row:
        return reject(f"No record with id={payload['id']}")

    current_status = row[1]
    if current_status in TERMINAL_STATES:
        return reject(f"Cannot score a terminal record (status={current_status})")

    ts = now_utc()
    sets = ["score_pct=?", "updated_at=?"]
    vals = [score_pct, ts]

    if payload.get('track'):
        sets.append("track=?"); vals.append(payload['track'])
    if payload.get('comp'):
        sets.append("comp=?"); vals.append(payload['comp'])
    if payload.get('link'):
        sets.append("link=?"); vals.append(payload['link'])
    if payload.get('notes') is not None:
        sets.append("notes=?"); vals.append(payload['notes'])
    if payload.get('remote_status') is not None:
        sets.append("remote=?"); vals.append(payload['remote_status'])

    vals.append(payload['id'])
    cur.execute(f"UPDATE reviewed_postings SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    _touch_write_marker(conn)
    return {"result": "APPROVED", "data": {"id": payload['id']}, "message": f"Score updated (id={payload['id']}, score_pct={score_pct})"}


def handle_audit(conn):
    cur = conn.cursor()
    report = {}

    cur.execute("""
        SELECT id, company, role, status FROM reviewed_postings
        WHERE status IN ('Applied','Screening','Interview','Offer','Closed')
        AND (applied_date IS NULL OR applied_date='')
    """)
    report['missing_applied_date'] = [dict(zip(['id','company','role','status'], r)) for r in cur.fetchall()]

    cur.execute("""
        SELECT id, company, role, status FROM reviewed_postings
        WHERE status IN ('Applied','Screening','Interview','Offer','Closed')
        AND (applied_method IS NULL OR applied_method='')
    """)
    report['missing_applied_method'] = [dict(zip(['id','company','role','status'], r)) for r in cur.fetchall()]

    cur.execute("""
        SELECT company_key, role_key, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
        FROM reviewed_postings
        GROUP BY company_key, role_key HAVING cnt > 1
    """)
    report['duplicate_candidates'] = [dict(zip(['company_key','role_key','count','ids'], r)) for r in cur.fetchall()]

    valid_list = "('Pending','Reviewed','Queued','Applied','Screening','Interview','Offer','Pass','Closed')"
    cur.execute(f"SELECT id, company, role, status FROM reviewed_postings WHERE status NOT IN {valid_list}")
    report['invalid_status'] = [dict(zip(['id','company','role','status'], r)) for r in cur.fetchall()]

    cur.execute("SELECT id, company, role, score_pct FROM reviewed_postings WHERE score_pct < 0 OR score_pct > 100")
    report['invalid_score'] = [dict(zip(['id','company','role','score_pct'], r)) for r in cur.fetchall()]

    total_gaps = sum(len(v) for v in report.values())
    return {
        "result": "OK",
        "data": report,
        "message": f"Audit complete. {total_gaps} total issues found."
    }


def handle_age_pass(conn):
    cur = conn.cursor()
    ts = now_utc()

    cur.execute("""
        SELECT id, company, role, reviewed_at
        FROM reviewed_postings
        WHERE status = 'Reviewed'
        AND julianday('now') - julianday(reviewed_at) > 10
        AND julianday('now') - julianday(updated_at) > 10
    """)
    candidates = cur.fetchall()

    if not candidates:
        return {"result": "OK", "data": {"aged_out": []}, "message": "No stale Reviewed rows found."}

    aged_out = []
    for row in candidates:
        rid, company, role, reviewed_at = row
        cur.execute("""
            UPDATE reviewed_postings
            SET status='Pass',
                notes=CASE WHEN notes IS NULL OR notes='' THEN 'Aged out — no decision after 10 days'
                           ELSE notes || ' | Aged out — no decision after 10 days' END,
                updated_at=?,
                closed_at=?
            WHERE id=?
        """, (ts, ts, rid))
        aged_out.append({"id": rid, "company": company, "role": role, "reviewed_at": reviewed_at})

    conn.commit()
    if aged_out:
        _touch_write_marker(conn)
    return {
        "result": "OK",
        "data": {"aged_out": aged_out},
        "message": f"Aged out {len(aged_out)} stale Reviewed row(s)."
    }


VERIZON_STOP = re.compile(r'\bverizon\b', re.IGNORECASE)
NON_TARGET_ROLES = {'recruiter', 'recruiting coordinator', 'talent acquisition'}
PMP_HARD_REQ = re.compile(r'\bpmp\s+(required|must)\b', re.IGNORECASE)
UNDERLEVELED = {'intern', 'associate', 'entry level', 'entry-level'}


COMP_FLOOR = 130000
HOURLY_ANNUAL_MULT = 2080


def _parse_comp_ceiling(comp):
    """Extract the ceiling (max) annual salary from a freeform comp string.
    Returns int USD or None if the string is empty or unparseable.
    Handles: "$264K-$363K", "$80K-$105,843", "$55.50-$74/hr", "$120K",
    "$152,000-$215,000", "$29/hr", "". Hourly rates convert at 2080 h/yr.
    """
    if comp is None:
        return None
    if not isinstance(comp, str):
        return None
    s = comp.strip()
    if not s:
        return None

    is_hourly = '/hr' in s.lower() or '/hour' in s.lower() or 'per hour' in s.lower()

    # Match numbers (with optional commas/decimals) and an optional K suffix.
    tokens = re.findall(r'([\d,]+(?:\.\d+)?)\s*([Kk])?', s)
    values = []
    for num_str, k_suffix in tokens:
        num_str = num_str.strip(',')
        if not num_str or num_str == ',':
            continue
        try:
            num = float(num_str.replace(',', ''))
        except ValueError:
            continue
        if k_suffix:
            num *= 1000
        values.append(num)

    if not values:
        return None

    ceiling = max(values)
    if is_hourly:
        ceiling *= HOURLY_ANNUAL_MULT
    return int(ceiling)


def _comp_ceiling_fail(payload):
    ceiling = _parse_comp_ceiling(payload.get('comp'))
    if ceiling is not None and ceiling < COMP_FLOOR:
        return 'Comp below floor'
    return None


def _commute_fail(payload):
    remote = payload.get('remote')
    if remote is not None and remote == 0:
        location = payload.get('location', '').lower()
        if 'nj' not in location and 'new jersey' not in location and 'remote' not in location:
            return 'Non-remote, outside NJ'
    return None


def apply_filters(payload):
    for check in (_comp_ceiling_fail, _commute_fail):
        reason = check(payload)
        if reason:
            return reason
    return None


def handle_ingest(payload, conn):
    for field in ('company', 'role'):
        if not payload.get(field):
            return reject(f"Missing required field: {field}")

    company = payload['company']
    role = payload['role']
    jd_text = payload.get('jd_text', '')

    # --- filter gate ---
    fail_reason = None

    if VERIZON_STOP.search(company):
        fail_reason = 'Verizon hard stop'
    elif role.lower().strip() in NON_TARGET_ROLES:
        fail_reason = 'Non-target role'
    elif any(tag in role.lower() for tag in UNDERLEVELED):
        fail_reason = 'Underleveled'
    elif PMP_HARD_REQ.search(jd_text):
        fail_reason = 'PMP hard requirement'
    else:
        fail_reason = apply_filters(payload)

    status = 'Pass' if fail_reason else 'Pending'
    notes = f'Auto-filtered: {fail_reason}' if fail_reason else payload.get('notes')

    insert_payload = {**payload, 'status': status, 'notes': notes}
    return handle_insert(insert_payload, conn)


def handle_delete(payload, conn):
    cur = conn.cursor()

    if not payload.get('id'):
        return reject("id is required for delete")
    if not payload.get('confirm'):
        return reject("confirm:true is required for delete")

    cur.execute("SELECT id, company, role, status FROM reviewed_postings WHERE id=?", (payload['id'],))
    row = cur.fetchone()
    if not row:
        return reject(f"No record with id={payload['id']}")

    status = row[3]
    if status in APPLIED_STATES and not payload.get('force'):
        return reject(f"Record is {status} — force:true required to delete Applied+ records")

    cur.execute("DELETE FROM reviewed_postings WHERE id=?", (payload['id'],))
    conn.commit()
    _touch_write_marker(conn)
    return {"result": "OK", "data": {"id": row[0], "company": row[1], "role": row[2]},
            "message": f"Deleted id={row[0]}"}


def handle_backfill_closed_at(conn):
    """One-shot maintenance: set closed_at on terminal rows where it is NULL.

    age_pass historically did not set closed_at when transitioning Reviewed -> Pass.
    This backfills any terminal-state row (Pass or Closed) that is missing closed_at,
    using the row's updated_at as the best available approximation of when it closed.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, status, updated_at
        FROM reviewed_postings
        WHERE status IN ('Pass','Closed') AND closed_at IS NULL
    """)
    rows = cur.fetchall()
    if not rows:
        return {"result": "OK", "data": {"backfilled": []}, "message": "No terminal rows missing closed_at."}

    backfilled = []
    for rid, status, updated_at in rows:
        ts = updated_at or now_utc()
        cur.execute("UPDATE reviewed_postings SET closed_at=? WHERE id=? AND closed_at IS NULL", (ts, rid))
        if cur.rowcount:
            backfilled.append({"id": rid, "status": status, "closed_at": ts})

    conn.commit()
    if backfilled:
        _touch_write_marker(conn)
    return {
        "result": "OK",
        "data": {"backfilled": backfilled, "count": len(backfilled)},
        "message": f"Backfilled closed_at on {len(backfilled)} terminal row(s)."
    }


def handle_resolve_id(payload, conn):
    cur = conn.cursor()

    company = payload.get('company', '')
    role = payload.get('role', '')
    if not company or not role:
        return reject("company and role are required for resolve_id")

    ck = company_key(company)
    rk = role_key(role)

    cur.execute(
        "SELECT id, company, role, status FROM reviewed_postings WHERE company_key=? AND role_key=?",
        (ck, rk)
    )
    rows = cur.fetchall()

    if len(rows) == 1:
        r = rows[0]
        return {"result": "OK", "data": {"id": r[0], "company": r[1], "role": r[2], "status": r[3]},
                "message": f"Resolved to id={r[0]}"}
    elif len(rows) > 1:
        matches = [{"id": r[0], "company": r[1], "role": r[2], "status": r[3]} for r in rows]
        return {"result": "AMBIGUOUS", "data": {"matches": matches},
                "message": f"{len(rows)} exact matches found"}

    # Fuzzy fallback: same company, token-Jaccard on role_key >= 0.7
    cur.execute(
        "SELECT id, company, role, role_key, status FROM reviewed_postings WHERE company_key=?",
        (ck,)
    )
    candidates = cur.fetchall()
    if candidates:
        target_tokens = _token_set(rk)
        best = None
        best_sim = 0.0
        for r in candidates:
            cand_tokens = _token_set(r[3])
            # Require at least 3 tokens on each side to avoid trivial matches
            if len(target_tokens) < 3 or len(cand_tokens) < 3:
                continue
            sim = _similarity(target_tokens, cand_tokens)
            if sim > best_sim:
                best_sim = sim
                best = r
        if best and best_sim >= 0.85:
            return {"result": "OK",
                    "data": {"id": best[0], "company": best[1], "role": best[2],
                             "status": best[4], "match": "fuzzy",
                             "similarity": round(best_sim, 2)},
                    "message": f"Resolved to id={best[0]} via fuzzy match (sim={best_sim:.2f})"}

    return {"result": "NOT_FOUND", "data": {},
            "message": f"No match for company_key='{ck}', role_key='{rk}'"}


MONITOR_DB_PATH = r"C:/Users/Garrison/career/monitor.db"


def handle_resolve_flag(payload):
    if not payload.get('id'):
        return reject("id is required for resolve_flag")

    conn = sqlite3.connect(MONITOR_DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id, flag_type, severity, resolved_at FROM quality_flags WHERE id=?", (payload['id'],))
    row = cur.fetchone()
    if not row:
        conn.close()
        return reject(f"No quality_flag with id={payload['id']}")
    if row[3] is not None:
        conn.close()
        return {"result": "OK", "data": {"id": row[0]}, "message": f"Already resolved at {row[3]}"}

    ts = now_utc()
    cur.execute("UPDATE quality_flags SET resolved_at=? WHERE id=?", (ts, payload['id']))
    conn.commit()
    conn.close()
    return {"result": "OK", "data": {"id": row[0], "flag_type": row[1], "severity": row[2]},
            "message": f"Flag id={row[0]} resolved at {ts}"}


def handle_write_flag(payload):
    for field in ('agent_name', 'flag_type', 'description'):
        if not payload.get(field):
            return reject(f"{field} is required for write_flag")

    severity = payload.get('severity', 'medium')
    if severity not in ('low', 'medium', 'high'):
        return reject(f"severity must be low/medium/high, got: {severity}")

    session_id = payload.get('session_id', '')
    ts = now_utc()

    conn = sqlite3.connect(MONITOR_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quality_flags (session_id, timestamp, agent_name, flag_type, description, severity) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, ts, payload['agent_name'], payload['flag_type'], payload['description'], severity),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "result": "OK",
        "data": {"id": new_id, "flag_type": payload['flag_type'], "severity": severity},
        "message": f"Flag id={new_id} written ({payload['flag_type']}, {severity})",
    }


def handle_event_log_write(payload):
    for field in ('agent_name', 'event_type'):
        if not payload.get(field):
            return reject(f"{field} is required for event_log_write")

    session_id   = payload.get('session_id', '')
    event_detail = payload.get('event_detail', '')
    result_val   = payload.get('result', 'pass')
    ts = now_utc()

    conn = sqlite3.connect(MONITOR_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO event_log (session_id, timestamp, agent_name, event_type, event_detail, result) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, ts, payload['agent_name'], payload['event_type'], event_detail, result_val),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "result": "OK",
        "data": {"id": new_id, "event_type": payload['event_type']},
        "message": f"Event id={new_id} written ({payload['event_type']}, {result_val})",
    }


def handle_bulk_resolve(payload, conn):
    items = payload.get('items')
    if not items or not isinstance(items, list):
        return reject("items (list of {company, role}) is required for bulk_resolve")

    results = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item.get('company') or not item.get('role'):
            results.append({"index": i, "result": "REJECTED",
                            "message": "company and role are required",
                            "input": item})
            continue
        sub = handle_resolve_id({"company": item['company'], "role": item['role']}, conn)
        results.append({
            "index": i,
            "company": item['company'],
            "role": item['role'],
            "result": sub.get('result'),
            "data": sub.get('data', {}),
            "message": sub.get('message', ''),
        })

    counts = {}
    for r in results:
        counts[r['result']] = counts.get(r['result'], 0) + 1

    return {
        "result": "OK",
        "data": {"count": len(results), "by_result": counts, "results": results},
        "message": f"bulk_resolve processed {len(results)} item(s): {counts}",
    }


def ensure_constraints(conn):
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_company_role "
        "ON reviewed_postings (company_key, role_key)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS write_marker (
            id INTEGER PRIMARY KEY CHECK (id=1),
            last_write_at TEXT,
            write_count INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO write_marker (id, last_write_at, write_count) VALUES (1, ?, 0)",
        (now_utc(),)
    )
    conn.commit()


def _touch_write_marker(conn):
    try:
        conn.execute(
            "UPDATE write_marker SET last_write_at=?, write_count=write_count+1 WHERE id=1",
            (now_utc(),)
        )
        conn.commit()
    except Exception:
        pass


def _token_set(key_str):
    return set(key_str.split()) if key_str else set()


def _jaccard(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similarity(a, b):
    """Blend Jaccard with overlap coefficient so subset-style matches score high."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    overlap = inter / min(len(a), len(b))  # how much of the shorter is in the longer
    jaccard = inter / len(a | b)
    return max(overlap, jaccard)


STAGING_PATH = r"C:/Users/Garrison/career/scan-staging.json"


def handle_mark_for_rescore(payload, conn):
    """Reset Reviewed rows back to Pending so /job-match re-scores them.

    Payload selectors (one required):
      selector: "legacy-no-verdict" — all Reviewed rows where tldr IS NULL
      selector: "by-id" with ids: [<int>, ...] — specific row ids

    Behavior:
      - Sets status='Pending' on matching rows.
      - Clears all verdict columns (tldr, gate_status, gate_failures, 4 component
        scores, recency_multiplier, met/unmet json, soft_reqs, hidden_signals,
        seniority_calibration, resume_used) and score_pct so the rescore writes fresh data.
      - Skips rows in terminal states (Pass, Closed) for safety.
      - Re-appends each row to scan-staging.json so /job-match's normal queue flow picks them up.

    The reviewed_at timestamp is preserved (when the row was first reviewed).
    updated_at is refreshed to the rescore-flag time.
    """
    import os
    cur = conn.cursor()

    selector = payload.get('selector')
    ids = payload.get('ids')

    if selector == 'legacy-no-verdict':
        cur.execute("""
            SELECT id, company, role, comp, link, source, staffing_agency, notes
            FROM reviewed_postings
            WHERE status='Reviewed' AND tldr IS NULL
        """)
    elif selector == 'by-id' or (ids and not selector):
        if not ids or not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            return reject("'by-id' selector requires 'ids' as a list of integers")
        placeholders = ','.join('?' * len(ids))
        cur.execute(f"""
            SELECT id, company, role, comp, link, source, staffing_agency, notes
            FROM reviewed_postings
            WHERE id IN ({placeholders}) AND status NOT IN ('Pass', 'Closed')
        """, ids)
    else:
        return reject(f"Unknown or missing selector: {selector!r}. Expected 'legacy-no-verdict' or 'by-id' (with ids list).")

    rows = cur.fetchall()
    if not rows:
        return {"result": "OK", "data": {"reset_count": 0, "ids": []}, "message": "No rows matched the selector."}

    row_ids = [r[0] for r in rows]
    placeholders = ','.join('?' * len(row_ids))
    ts = now_utc()
    cur.execute(f"""
        UPDATE reviewed_postings
        SET status='Pending',
            tldr=NULL, gate_status=NULL, gate_failures=NULL,
            hard_skills_score=NULL, experience_score=NULL,
            domain_score=NULL, leadership_score=NULL,
            recency_multiplier=NULL,
            met_json=NULL, unmet_json=NULL,
            soft_reqs=NULL, hidden_signals=NULL,
            seniority_calibration=NULL, resume_used=NULL,
            score_pct=NULL,
            updated_at=?
        WHERE id IN ({placeholders})
    """, [ts] + row_ids)

    # Re-append to scan-staging.json so /job-match picks them up via its normal queue
    staging_path = STAGING_PATH
    if os.path.exists(staging_path):
        try:
            with open(staging_path, 'r', encoding='utf-8') as f:
                staging = json.load(f)
            if not isinstance(staging, list):
                staging = []
        except (json.JSONDecodeError, OSError):
            staging = []
    else:
        staging = []

    # De-duplicate: skip rows already present in staging by canonical_link
    existing_links = {s.get('canonical_link', '') for s in staging if isinstance(s, dict)}

    added = 0
    for r in rows:
        link = r[4] or ''
        if link and link in existing_links:
            continue
        staging.append({
            "source_email": "(rescore-from-db)",
            "company": r[1] or "",
            "role_title": r[2] or "",
            "comp": r[3] or "",
            "location": "",
            "remote_status": "",
            "canonical_link": link,
            "staffing_agency": str(r[6]).lower() in ('1', 'true', 'yes'),
            "inferred_employer": r[1] or "",
            "notes": ((r[7] or "") + " [flagged for rescore]").strip(),
        })
        existing_links.add(link)
        added += 1

    try:
        with open(staging_path, 'w', encoding='utf-8') as f:
            json.dump(staging, f, indent=2)
    except OSError as e:
        # Roll back the DB change so we don't desync state
        conn.rollback()
        return reject(f"Failed to write staging file: {e}")

    conn.commit()
    _touch_write_marker(conn)

    return {
        "result": "APPROVED",
        "data": {"reset_count": len(row_ids), "ids": row_ids, "added_to_staging": added},
        "message": f"Reset {len(row_ids)} rows to Pending; added {added} new entries to scan-staging.json."
    }


def handle_write_review(payload, conn):
    """Atomically write a structured review verdict to a row.

    Required payload fields:
      id, tldr, gate_status (PASS|FAIL),
      hard_skills_score, experience_score, domain_score, leadership_score (int 0-100),
      recency_multiplier (1.00 | 0.75 | 0.50 | 0.25),
      met (list of str), unmet (list of str),
      soft_reqs, hidden_signals, seniority_calibration (str),
      resume_used (pm|automation|both)

    Optional:
      gate_failures (list of str - required when gate_status=FAIL),
      comp, link, notes

    Behavior:
      - Computes score_pct from components (FAIL caps at 35).
      - Transitions status Pending -> Reviewed.
      - Rejects writes to terminal records (Pass, Closed).
    """
    cur = conn.cursor()

    required = [
        'id', 'tldr', 'gate_status',
        'hard_skills_score', 'experience_score', 'domain_score', 'leadership_score',
        'recency_multiplier', 'met', 'unmet',
        'soft_reqs', 'hidden_signals', 'seniority_calibration', 'resume_used',
    ]
    missing = [k for k in required if k not in payload]
    if missing:
        return reject(f"Missing required fields for write_review: {missing}")

    for k in ('hard_skills_score', 'experience_score', 'domain_score', 'leadership_score'):
        v = payload[k]
        if not isinstance(v, int) or not (0 <= v <= 100):
            return reject(f"{k} must be integer 0-100, got: {v!r}")

    if payload['gate_status'] not in ('PASS', 'FAIL'):
        return reject(f"gate_status must be PASS or FAIL, got: {payload['gate_status']!r}")
    gate_failures = payload.get('gate_failures')
    if payload['gate_status'] == 'FAIL':
        if not gate_failures or not isinstance(gate_failures, list) or not all(isinstance(x, str) for x in gate_failures):
            return reject("gate_failures must be a non-empty list of strings when gate_status=FAIL")
    else:
        gate_failures = None

    if payload['recency_multiplier'] not in (1.00, 0.75, 0.50, 0.25):
        return reject(f"recency_multiplier must be one of 1.00, 0.75, 0.50, 0.25; got: {payload['recency_multiplier']!r}")

    if payload['resume_used'] not in ('pm', 'automation', 'both'):
        return reject(f"resume_used must be pm, automation, or both; got: {payload['resume_used']!r}")

    for k in ('met', 'unmet'):
        v = payload[k]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return reject(f"{k} must be a list of strings, got: {type(v).__name__}")

    cur.execute("SELECT id, status FROM reviewed_postings WHERE id=?", (payload['id'],))
    row = cur.fetchone()
    if not row:
        return reject(f"No record with id={payload['id']}")
    current_status = row[1]
    if current_status in TERMINAL_STATES:
        return reject(f"Cannot write review to terminal record (status={current_status})")

    rm = payload['recency_multiplier']
    score_pct = round(
        0.35 * payload['hard_skills_score'] +
        0.30 * payload['experience_score'] * rm +
        0.20 * payload['domain_score'] +
        0.15 * payload['leadership_score']
    )
    if payload['gate_status'] == 'FAIL':
        score_pct = min(score_pct, 35)

    ts = now_utc()

    sets = [
        "tldr=?", "gate_status=?", "gate_failures=?",
        "hard_skills_score=?", "experience_score=?", "domain_score=?", "leadership_score=?",
        "recency_multiplier=?",
        "met_json=?", "unmet_json=?",
        "soft_reqs=?", "hidden_signals=?", "seniority_calibration=?",
        "resume_used=?",
        "score_pct=?",
        "reviewed_at=COALESCE(reviewed_at, ?)",
        "updated_at=?",
    ]
    vals = [
        payload['tldr'], payload['gate_status'],
        json.dumps(gate_failures) if gate_failures else None,
        payload['hard_skills_score'], payload['experience_score'],
        payload['domain_score'], payload['leadership_score'],
        rm,
        json.dumps(payload['met']), json.dumps(payload['unmet']),
        payload['soft_reqs'], payload['hidden_signals'], payload['seniority_calibration'],
        payload['resume_used'],
        score_pct,
        ts, ts,
    ]

    if payload.get('comp'):
        sets.append("comp=?"); vals.append(payload['comp'])
    if payload.get('link'):
        sets.append("link=?"); vals.append(payload['link'])
    if payload.get('notes') is not None:
        sets.append("notes=?"); vals.append(payload['notes'])

    # Status transition:
    # - Gate FAIL → auto-Pass (parallel to ingest's filter-fail auto-pass behavior).
    #   The full verdict is preserved on the row; the gate-fail reasons are folded
    #   into notes so the row carries its closure rationale.
    # - Gate PASS, currently Pending → Reviewed.
    # - Otherwise → no status change.
    if payload['gate_status'] == 'FAIL' and current_status not in TERMINAL_STATES:
        sets.append("status=?"); vals.append('Pass')
        sets.append("closed_at=?"); vals.append(ts)
        gate_note = "Gate FAIL — " + ("; ".join(gate_failures) if gate_failures else "auto-pass on gate failure")
        existing_note = payload.get('notes')
        merged_note = f"{existing_note} | {gate_note}" if existing_note else gate_note
        # If we already appended notes above, replace it; otherwise add it now.
        if "notes=?" in sets:
            idx = sets.index("notes=?")
            vals[idx] = merged_note
        else:
            sets.append("notes=?"); vals.append(merged_note)
    elif current_status == 'Pending':
        sets.append("status=?"); vals.append('Reviewed')

    vals.append(payload['id'])
    cur.execute(f"UPDATE reviewed_postings SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    _touch_write_marker(conn)

    final_status = 'Pass' if (payload['gate_status'] == 'FAIL' and current_status not in TERMINAL_STATES) \
                   else ('Reviewed' if current_status == 'Pending' else current_status)
    return {
        "result": "APPROVED",
        "data": {"id": payload['id'], "score_pct": score_pct, "gate_status": payload['gate_status'], "status": final_status},
        "message": f"Review written (id={payload['id']}, score_pct={score_pct}, gate={payload['gate_status']}, status={final_status})"
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action',  required=True, choices=['insert','ingest','update_status','update_score','write_review','mark_for_rescore','audit','age_pass','resolve_id','bulk_resolve','delete','resolve_flag','write_flag','event_log_write','backfill_closed_at'])
    parser.add_argument('--payload', default='{}')
    args = parser.parse_args()

    payload = json.loads(args.payload)

    # monitor.db actions: do not open job-tracker.db
    if args.action in ('resolve_flag', 'write_flag', 'event_log_write'):
        try:
            if args.action == 'resolve_flag':
                result = handle_resolve_flag(payload)
            elif args.action == 'write_flag':
                result = handle_write_flag(payload)
            else:
                result = handle_event_log_write(payload)
        except Exception as e:
            result = {"result": "ERROR", "data": {}, "message": str(e)}
        print(json.dumps(result, indent=2))
        return

    conn = sqlite3.connect(DB_PATH)

    try:
        if args.action not in ('audit', 'delete'):
            ensure_constraints(conn)

        # Always run age_pass silently on every invocation
        if args.action != 'age_pass':
            handle_age_pass(conn)

        if args.action == 'insert':
            result = handle_insert(payload, conn)
        elif args.action == 'update_status':
            result = handle_update_status(payload, conn)
        elif args.action == 'update_score':
            result = handle_update_score(payload, conn)
        elif args.action == 'write_review':
            result = handle_write_review(payload, conn)
        elif args.action == 'mark_for_rescore':
            result = handle_mark_for_rescore(payload, conn)
        elif args.action == 'age_pass':
            result = handle_age_pass(conn)
        elif args.action == 'ingest':
            result = handle_ingest(payload, conn)
        elif args.action == 'resolve_id':
            result = handle_resolve_id(payload, conn)
        elif args.action == 'bulk_resolve':
            result = handle_bulk_resolve(payload, conn)
        elif args.action == 'delete':
            result = handle_delete(payload, conn)
        elif args.action == 'audit':
            result = handle_audit(conn)
        elif args.action == 'backfill_closed_at':
            result = handle_backfill_closed_at(conn)
    except Exception as e:
        result = {"result": "ERROR", "data": {}, "message": str(e)}
    finally:
        conn.close()

    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
