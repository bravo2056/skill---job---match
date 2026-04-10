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


def company_key(raw):
    if not raw: return ''
    return re.sub(r'\s+', ' ', re.sub(r'[.,\-&]', '', raw.lower().strip()))

def role_key(raw):
    if not raw: return ''
    key = raw.lower().strip()
    key = re.sub(r'\bsenior\b', 'sr', key)
    key = re.sub(r'\bsr\.', 'sr', key)
    key = re.sub(r'\bjunior\b', 'jr', key)
    return re.sub(r'\s+', ' ', key)

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action',  required=True, choices=['insert','update_status','update_score','audit','age_pass'])
    parser.add_argument('--payload', default='{}')
    args = parser.parse_args()

    payload = json.loads(args.payload)
    conn    = sqlite3.connect(DB_PATH)

    try:
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
    except Exception as e:
        result = {"result": "ERROR", "data": {}, "message": str(e)}
    finally:
        conn.close()

    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
