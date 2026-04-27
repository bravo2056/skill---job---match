# Read config.md before proceeding.

## Email Scanning (Gmail — "Job search 2026" label)

### Phase 1 — Enumerate and Extract (no scoring)

**Step 0:** Run `C:/Users/Garrison/career/launch-dashboard.bat` via bash to start the live dashboard (backend :8001, frontend :5173). The script is idempotent — it skips any service already running. Log completion before proceeding.
If `C:/Users/Garrison/career/scan-staging.json` already exists at scan start:
- Overwrite the file with an empty JSON array `[]`
- Log the overwrite event to event_log before proceeding

If `C:/Users/Garrison/career/rejection-staging.json` already exists at scan start:
- Overwrite the file with an empty JSON array `[]`
- Log the overwrite event to event_log before proceeding

**Step 1:** Call `search_threads` with `label:"Job search 2026"`. Retrieve thread IDs and subjects only. Do not read any bodies yet. Log the total count.

**Step 2:** Evaluate each thread by subject line and sender. Route as follows:
- **Digest or ambiguous** → Step 3 (extract roles).
- **Rejection** → Step 3R (match and stage close).
- **Other non-digest** (connection acceptance, application confirmation, recruiter message, news with no job listings) → skip.

A message is a rejection candidate when **either** holds:
- Subject (case-insensitive) contains any of: `regarding your application`, `your application to`, `your application status`, `update on your application`, `we have decided`, `moved forward with other candidates`, `not moving forward`, `no longer being considered`, `unable to offer`, `decided to pursue other`, `position has been filled`, `we will not be moving forward`.
- Sender is a known ATS domain — `greenhouse.io`, `greenhouse-mail.io`, `ashbyhq.com`, `lever.co`, `workday.com`, `myworkday.com`, `icims.com`, `smartrecruiters.com`, `successfactors.com`, `taleo.net`, `bamboohr.com` — and the subject is generic application correspondence.

If a subject matches both a digest pattern and a rejection pattern, treat as digest (Step 3) — rejections from aggregator digests are out of scope.

**Step 3:** For digest and ambiguous threads only, call `get_thread` one at a time. Immediately upon reading, extract job data into normalized rows and discard the raw body. Never hold more than one raw thread body in context simultaneously.

Row schema:
`source_email | company | role_title | comp | location | remote_status | canonical_link | staffing_agency (bool) | inferred_employer | notes`

Immediately after extracting rows from each email, write those rows directly into
`C:/Users/Garrison/career/scan-staging.json` in the same step. Do not wait until the end of
Phase 1. If the file does not exist, create it. If it exists, it has already been reset at scan start.
Append new rows to the existing array in place. Do not create helper scripts, temp scripts, sidecar append utilities,
or delayed write buffers. Do not stage rows anywhere else first. This direct per-email
write is mandatory so extracted rows survive context compaction.

**Step 3R:** For rejection candidates only, call `get_thread` one at a time. Extract:
- `company` — from sender domain, signature block, or body header
- `role_title` — from subject or body (often phrased "your application for <role>")
- `received_date` — message date in `YYYY-MM-DD`
- `sender` — full From header (for cleanup list)
- `subject` — for cleanup list

Discard the raw body immediately after extraction. The same one-body-at-a-time rule applies.

Stage the rejection record in `C:/Users/Garrison/career/rejection-staging.json` with `resolved_id:null`, `prior_status:null`, `match_type:null` — these are filled in by the batch resolve step at end of Phase 1. Schema:
`{thread_id, sender, subject, received_date, company, role_title, resolved_id, prior_status, match_type}`

**Step 3R-batch:** After all rejection candidates have been staged (end of Phase 1, before the completion notice), call once:

`python integrity.py --action bulk_resolve --payload '{"items":[{"company":"<c1>","role":"<r1>"}, ...]}'`

Pass every staged rejection's company + role in order. For each result, update the corresponding row in `rejection-staging.json` with `resolved_id`, `prior_status`, and `match_type` per the resolution-handling rules below. Do not call `resolve_id` per-row.

Resolution handling — every rejection is recorded in `rejection-staging.json`. The `match_type` controls whether Phase 2 performs a status update:

- **Exact match, prior_status in Applied / Screening / Interview / Offer** → `match_type:"exact"`. Phase 2 will close the row.
- **Exact match, prior_status in Pending / Reviewed / Queued (never applied)** → `match_type:"not_applied"`. Phase 2 will NOT update the row (integrity.py rejects Closed transitions without applied_date). Log to event_log immediately: `event_type:"rejection_not_applied"`, `event_detail:"<sender> | <subject> | id=<resolved_id>, prior_status=<status>"`.
- **Exact match, prior_status terminal (Pass or Closed)** → `match_type:"already_terminal"`. Phase 2 will NOT update the row. Surface in Phase 1 notice.
- **Fuzzy match, single unambiguous candidate, prior_status in Applied / Screening / Interview / Offer** → `match_type:"fuzzy"`. Phase 2 will close the row.
- **Fuzzy match, single unambiguous candidate, prior_status in Pending / Reviewed / Queued** → `match_type:"not_applied"`. Same handling as the exact-match not-applied case above.
- **Fuzzy match, single unambiguous candidate, prior_status terminal** → `match_type:"already_terminal"`. Same handling as the exact-match terminal case above.
- **Fuzzy match, multiple candidates** OR **no match** → set `resolved_id:null`, `match_type:"unmatched"`. Log to event_log: `event_type:"rejection_unmatched"`, `event_detail:"<sender> | <subject>"`.

Applied+ status set for this routing: `{Applied, Screening, Interview, Offer}`. Closed and Pass are terminal and route to `already_terminal`. Pending, Reviewed, and Queued route to `not_applied`.

Never auto-create a new DB row from a rejection email. Never modify the row from Phase 1; the close happens only in Phase 2 after explicit confirmation.

**Step 4:** Backpressure rule — strict execution contract (all messages)

If a message is large enough to trigger context pressure, truncated reads, or tool warnings (commonly ~50KB+), or cannot be fully processed in a single pass, execute the following steps in exact order with no deviation:

1. Extract up to 25 roles from the readable portion only.
2. Write those rows to `scan-staging.json` immediately. This write must occur before any further tool calls or message processing.
3. Append an entry to `partial_messages` in the Phase 1 summary:
   ```json
   {
     "thread_id": "<id>",
     "extracted_count": "<n>",
     "skipped_rows": "<m>"
   }
   ```
4. Stop processing this message.
5. Continue to the next message.

Hard constraints:

- Never read this message again in the same run.
- Never attempt a second extraction pass.
- Never exceed 25 roles under any condition.
- Never infer or reconstruct skipped rows.
- Never perform additional analysis on skipped content.

Malformed definition — explicit triggers only:
A message is considered malformed if required fields (title or link) cannot be consistently identified across entries, or if the structure is truncated or broken such that sequential extraction cannot proceed. HTML quality and encoding issues alone do not qualify.

Token limit handling:
If a token limit error occurs, treat it as a fallback trigger. Execute the backpressure sequence using whatever portion of the message remains accessible.

Completion rule:
The message is considered complete after partial processing and must not be revisited in the current run. Rows already written to `scan-staging.json` proceed through Phase 2 (ingest, dedup, scoring) normally.

Note on duplication:
This rule intentionally reinforces existing limits (25-role cap, immediate write) and overrides normal flow when triggered.

**Step 4a:** Link extraction — extract URLs by pattern within each role block, not by visual layout inference.

For each extracted role, identify the contiguous region of the email body from which the role was parsed (the "role block" — the same region used to read company, title, comp, location, and tags). Within that block only, scan for URLs matching the sender's approved canonical pattern. Apply the sender's filter. Deduplicate the matched URLs. Strip query parameters from each.

**Resolution:**
- If the block contains exactly one distinct approved URL after dedupe, store it as `canonical_link`.
- If the block contains zero approved URLs, store `canonical_link` empty and call `python integrity.py --action write_flag --payload '{"agent_name":"email-scanner","flag_type":"link_missing","severity":"low","description":"<company> | <role_title>","session_id":"<scan_session_id>"}'`.
- If the block contains more than one distinct approved URL, store `canonical_link` empty and call `python integrity.py --action write_flag --payload '{"agent_name":"email-scanner","flag_type":"link_ambiguous","severity":"low","description":"<company> | <role_title>","session_id":"<scan_session_id>"}'`.

Never guess between candidate URLs. Sequential fallback, positional mapping, and adjacent-role tolerance are prohibited.

`canonical_link` must be either a URL exactly matching an approved pattern (with query parameters stripped) or empty. Never store placeholder text, sender names, bare domains, base paths, partial URLs, or any URL that does not match an approved pattern. If a URL does not match an approved pattern, discard it — never use it as a fallback.

For non-aggregator senders not listed below, extract the link from the role's anchor, strip tracking parameters, and store under the same storage rule. The previous "store base URL only" fallback is removed.

Do not store multiple URL variants of the same posting.

**Approved canonical patterns:**

| Sender (`source_email`) | Pattern | Filter |
|---|---|---|
| `noreply@jobright.ai` | `https://jobright\.ai/jobs/info/[a-f0-9]{24}` | None |
| `alerts@ziprecruiter.com` | `https://www\.ziprecruiter\.com/(?:km\|ekm)/[A-Za-z0-9_-]+` | None |
| `ali@hiring.cafe` | `https://u52508838\.ct\.sendgrid\.net/ls/click\?[^"\s]+` | Anchor text must equal "Apply" (case-insensitive, trimmed) |
| `donotreply@match.indeed.com` | `https://cts\.indeed\.com/v3/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+` | URL must be preceded by "View job:" label (case-insensitive, trimmed) |

**Pattern lifecycle:** Aggregator URL patterns have a finite lifetime. Review `link_missing` and `link_ambiguous` flag counts weekly. A sudden rate increase for a single sender indicates a likely pattern change requiring an update to this table.

**Step 5:** For any row where `staffing_agency = true`, record the inferred employer name if identifiable. Process normally through ingest — integrity.py handles staffing agency roles the same as direct postings. Do not hold for confirmation.

**Step 6:** Backlog Warning Protocol — if total extracted rows exceed 150, pause and output exactly:

> "Extraction paused — [N] roles collected from [N] emails. This is a large backlog. Choose: (1) continue and score all, (2) score only roles from emails received in the last 7 days, (3) stop here and save Phase 1 extraction for staged scoring later, or (4) restart with a narrower date range."

Do not continue until the user selects an option. Do not infer a preference or default to any option.

**Step 7:** Output Phase 1 completion notice. Include only counts and staffing flags. No role listings, no summaries, no partial scoring:

> "Phase 1 complete — [N] emails processed, [N] digests read, [N] roles extracted, [N] roles capped or discarded, [N] duplicates flagged, [N] staffing-agency roles flagged (processed normally), [N] rejections detected ([N] matched, [N] not applied, [N] already terminal, [N] unmatched). Awaiting confirmation to begin Phase 2."

Write all extracted non-duplicate rows to `C:/Users/Garrison/career/scan-staging.json`
as a JSON array using the row schema. This file is transport only — it is the handoff
artifact for job-match. Do not treat it as a log or history file.

`rejection-staging.json` is the parallel transport artifact for rejection closes. Same lifecycle: written during Phase 1, consumed in Phase 2, not a history file.

Do not begin Phase 2 until the user explicitly confirms.

---

### Phase 2 — Write and Close

Only after explicit user confirmation:

**Step 1:** Write all non-flagged extracted rows to DB by calling `python integrity.py --action ingest --payload '<json>'`. Payload fields: `company`, `role`, `comp`, `location`, `remote_status`, `link`, `source`, `inferred_employer`, `notes`. Do not include `status`, `score`, `score_pct`, `score_label`, `verdict`, or `tier` — integrity.py determines status. Result codes: APPROVED (staged as Pending), AUTO-PASS (filtered by business rule, written as Pass), DUPLICATE (skipped), REJECTED (schema error).

**Step 2:** Log each write result — APPROVED, AUTO-PASS, DUPLICATE, or REJECTED — to event_log.

**Step 3:** For each rejection in `rejection-staging.json` with `match_type` of `exact` or `fuzzy`, call:

`python integrity.py --action update_status --payload '{"id":<resolved_id>,"status":"Closed","notes":"Rejection received <received_date> — <sender>"}'`

Log each rejection write to event_log:
- event_type: `rejection_close`
- event_detail: `id=<id>, prior_status=<prior_status>, received=<received_date>, match_type=<exact|fuzzy>`
- result: `pass` or `fail`

If integrity.py returns an error (e.g., row already in terminal state due to a race), log the failure with `result:"fail"` and continue. Do not retry, do not force-update.

Rows with `match_type` of `not_applied`, `already_terminal`, or `unmatched` are NOT written to DB in Phase 2. They were already logged in Phase 1 and are reflected in counts only. Do not retry them, do not attempt a forced close, do not change their status by any other means.

**Step 4:** Produce the manual cleanup list of all scanned digest emails and rejection emails.

**Step 5:** Confirm the dashboard is running. If not, run `C:/Users/Garrison/career/launch-dashboard.bat` via bash. The script is idempotent. Log completion.

**Step 6:** Output scan completion summary:
> "Scan complete — [N] emails processed, [N] digests read, [N] roles extracted, [N] staged as Pending, [N] auto-passed (filtered), [N] duplicates skipped, [N] rejected (schema). Rejections: [N] closed, [N] not applied, [N] already terminal, [N] unmatched."

**Hard rules — never violate:**
- Never create helper scripts, append utilities, temp writers, or any other improvised code to write staging rows
- Never delay a staging write until later in the scan once rows have been extracted from an email
- Never retain a raw email body after extraction is complete
- Never hold more than one raw email body in context at a time
- Never recover extracted data from JSONL session logs or transcript files
- Never dump raw email bodies to disk for any reason
- If context is lost mid-scan, stop and tell the user — do not attempt recovery
- Never score inline during Phase 1
- Never carry forward more than the normalized row schema per role
- Never include role listings, summaries, or partial scores in Phase 1 output
- Never exceed 25 extracted roles per email
- Never run DB duplicate checks per-email — batch only at end of Phase 1
- Never continue past the backlog warning without an explicit user selection
- Never write `score`, `score_pct`, `score_label`, `verdict`, or `tier` to DB from this skill
- Never auto-create a new DB row from a rejection email
- Never re-open or change status of a row already in Pass or Closed via a rejection
- Never modify any field other than `status` and `notes` from a rejection
- Never close a row from a fuzzy match with multiple candidates — log as unmatched
- Never close a row whose prior_status is not in {Applied, Screening, Interview, Offer} — log as `not_applied` instead. Do not synthesize an applied_date to bypass the integrity check.

---

## Post-Scan Inbox Cleanup

After delivering scan results, produce a cleanup list of all digest and rejection emails that were scanned.
Format as a simple list:

**Emails to move to Job search 2026/Scanned (manual):**
- [sender] | [subject] | [date]
- [sender] | [subject] | [date] | [REJECTION → id=<resolved_id>, status=Closed]
- [sender] | [subject] | [date] | [REJECTION → id=<resolved_id>, not applied (prior_status=<status>) — no DB change]
- [sender] | [subject] | [date] | [REJECTION → id=<resolved_id>, already terminal — no DB change]
- [sender] | [subject] | [date] | [REJECTION → unmatched]

Non-digest, non-rejection emails (LinkedIn connection acceptances, messages, news) do not appear on this list.
Present the list at the end of every scan so the user can move them in one pass.

## Session Logging

All event_log writes route through:
`python integrity.py --action event_log_write --payload '{"agent_name":"email-scanner","session_id":"<scan_session_id>","event_type":"<type>","event_detail":"<detail>","result":"pass|fail"}'`

Direct sqlite3 writes to monitor.db are not used for event_log entries.

scan_metrics is written directly to monitor.db (no integrity.py wrapper for metrics).

At scan start, write to event_log via integrity.py:
- event_type: "scan_start"
- event_detail: label scanned, number of threads retrieved
- result: "pass"

At scan completion, INSERT one row into scan_metrics with these columns (others left NULL):
- session_id, date, emails_processed, duration_seconds
- roles_extracted, pending_written, duplicates_skipped, rejected
- rejections_closed, rejections_not_applied, rejections_already_terminal, rejections_unmatched

At DB write completion, write to event_log via integrity.py:
- event_type: "db_write"
- event_detail: "Routed insert through integrity.py — result: [APPROVED/AUTO-PASS/DUPLICATE/REJECTED]"
- result: "pass" or "fail"

At rejection close, write to event_log via integrity.py:
- event_type: "rejection_close"
- event_detail: "id=<id>, prior_status=<status>, received=<date>, match_type=<exact|fuzzy>"
- result: "pass" or "fail"

For not-applied rejections, write to event_log via integrity.py:
- event_type: "rejection_not_applied"
- event_detail: "<sender> | <subject> | id=<id>, prior_status=<status>"
- result: "pass"

For unmatched rejections, write to event_log via integrity.py:
- event_type: "rejection_unmatched"
- event_detail: "<sender> | <subject>"
- result: "pass"

If any email classification produces unexpected results, write to quality_flags via integrity.py write_flag:
- flag_type: "hallucination"
- severity: "medium"
