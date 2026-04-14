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
}


def company_key(raw):
    if not raw: return ''
    return re.sub(r'\s+', ' ', re.sub(r'[.,\-&]', '', raw.lower().strip()))

def role_key(raw):
    if not raw: return ''
    key = re.sub(r'[^\w\s]', '', raw.lower().strip())
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
        payload.get('remote', 1), payload.get('link'), payload.get('source'),
        payload.get('track'), payload.get('full_jd_reviewed', 0),
        payload.get('staffing_agency'), payload.get('notes')
    ))
    conn.commit()
    new_id = cur.lastrowid
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
        AND updated_at = reviewed_at
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
                updated_at=?
            WHERE id=?
        """, (ts, rid))
        aged_out.append({"id": rid, "company": company, "role": role, "reviewed_at": reviewed_at})

    conn.commit()
    return {
        "result": "OK",
        "data": {"aged_out": aged_out},
        "message": f"Aged out {len(aged_out)} stale Reviewed row(s)."
    }


VERIZON_STOP = re.compile(r'\bverizon\b', re.IGNORECASE)
NON_TARGET_ROLES = {'recruiter', 'recruiting coordinator', 'talent acquisition'}
PMP_HARD_REQ = re.compile(r'\bpmp\s+(required|must)\b', re.IGNORECASE)
UNDERLEVELED = {'intern', 'associate', 'entry level', 'entry-level'}


def _comp_ceiling_fail(payload):
    comp = payload.get('comp')
    if comp is not None and comp < 80000:
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
    return {"result": "OK", "data": {"id": row[0], "company": row[1], "role": row[2]},
            "message": f"Deleted id={row[0]}"}


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
    else:
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


def ensure_constraints(conn):
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_company_role "
        "ON reviewed_postings (company_key, role_key)"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action',  required=True, choices=['insert','ingest','update_status','update_score','audit','age_pass','resolve_id','delete','resolve_flag'])
    parser.add_argument('--payload', default='{}')
    args = parser.parse_args()

    payload = json.loads(args.payload)

    # resolve_flag operates on monitor.db, not job-tracker.db
    if args.action == 'resolve_flag':
        try:
            result = handle_resolve_flag(payload)
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
        elif args.action == 'age_pass':
            result = handle_age_pass(conn)
        elif args.action == 'ingest':
            result = handle_ingest(payload, conn)
        elif args.action == 'resolve_id':
            result = handle_resolve_id(payload, conn)
        elif args.action == 'delete':
            result = handle_delete(payload, conn)
        elif args.action == 'audit':
            result = handle_audit(conn)
    except Exception as e:
        result = {"result": "ERROR", "data": {}, "message": str(e)}
    finally:
        conn.close()

    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
