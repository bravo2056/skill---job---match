# Read config.md before proceeding.

## Email Scanning (Gmail — "Job search 2026" label)

### Phase 1 — Enumerate and Extract (no scoring)

**Step 0:** Run `python C:/Users/Garrison/career/dashboard.py --file C:/Users/Garrison/career/dashboard.html` via bash. This generates the pre-scan dashboard reflecting current DB state. Log completion before proceeding.
If `C:/Users/Garrison/career/scan-staging.json` already exists at scan start:
- Overwrite the file with an empty JSON array `[]`
- Log the overwrite event to event_log before proceeding

**Step 1:** Call `gmail_search_messages` with `label:"Job search 2026"`. Retrieve message IDs and subjects only. Do not read any bodies yet. Log the total count.

**Step 2:** Evaluate each message by subject line only. Skip on clear non-digest signals: connection acceptance, application confirmation, recruiter message, news with no job listings. If subject is ambiguous, treat as digest and proceed to Step 3.

**Step 3:** For digest and ambiguous messages only, call `gmail_read_message` one at a time. Immediately upon reading, extract job data into normalized rows and discard the raw body. Never hold more than one raw email body in context simultaneously.

Row schema:
`source_email | company | role_title | comp | location | remote_status | canonical_link | staffing_agency (bool) | inferred_employer | notes`

Immediately after extracting rows from each email, write those rows directly into
`C:/Users/Garrison/career/scan-staging.json` in the same step. Do not wait until the end of
Phase 1. If the file does not exist, create it. If it exists, it has already been reset at scan start.
Append new rows to the existing array in place. Do not create helper scripts, temp scripts, sidecar append utilities,
or delayed write buffers. Do not stage rows anywhere else first. This direct per-email
write is mandatory so extracted rows survive context compaction.

**Step 4:** Body handling rules — apply to every digest read:
- If body exceeds ~50KB, extract only: job titles, company names, compensation, location, and links. Ignore all HTML scaffolding, footer text, legal copy, and newsletter formatting.
- Cap extraction at 25 roles per email. Prioritize by title keyword match: Program Manager, TPM, Technical Program Manager, Operations, Process, Automation, Workflow, Systems. Discard the remainder silently.
- Extract canonical job links by removing all tracking parameters (e.g., utm_*, ref, tracking IDs) before writing the row.
- Store only the cleaned canonical URL in `canonical_link`.
- Do not store raw tracking URLs under any condition.
- If a canonical form cannot be derived, store the base URL only (scheme + domain + path) and discard all query parameters.
- Do not store multiple URL variants of the same posting.

**Step 5:** For any row where `staffing_agency = true`, record the inferred employer name only and flag for confirmation. Do not expand, analyze, or reason further about that role. Do not score it.

**Step 6:** Backlog Warning Protocol — if total extracted rows exceed 150, pause and output exactly:

> "Extraction paused — [N] roles collected from [N] emails. This is a large backlog. Choose: (1) continue and score all, (2) score only roles from emails received in the last 7 days, (3) stop here and save Phase 1 extraction for staged scoring later, or (4) restart with a narrower date range."

Do not continue until the user selects an option. Do not infer a preference or default to any option.

**Step 7:** Output Phase 1 completion notice. Include only counts and staffing flags. No role listings, no summaries, no partial scoring:

> "Phase 1 complete — [N] emails processed, [N] digests read, [N] roles extracted, [N] roles capped or discarded, [N] duplicates flagged. Staffing agency flags pending confirmation: [employer names]. Awaiting confirmation to write Pending records."

Write all extracted non-duplicate rows to `C:/Users/Garrison/career/scan-staging.json`
as a JSON array using the row schema. This file is transport only — it is the handoff
artifact for job-match. Do not treat it as a log or history file.

Do not begin Phase 2 until the user explicitly confirms.

---

### Phase 2 — Write and Close

Only after explicit user confirmation:

**Step 1:** Write all non-duplicate, non-flagged extracted rows to DB by calling `python integrity.py --action insert --payload '<json>'`. Payload fields: `company`, `role`, `status` (`Pending`), `comp`, `link`, `source`, `notes`. Do not include `score`, `score_pct`, `score_label`, `verdict`, or `tier`.

**Step 2:** Log each write result — APPROVED, DUPLICATE, or REJECTED — to event_log.

**Step 3:** Produce the manual cleanup list of all scanned digest emails.

**Step 4:** Run `python C:/Users/Garrison/career/dashboard.py --file C:/Users/Garrison/career/dashboard.html` via bash. Log completion.

**Step 5:** Output scan completion summary:
> "Scan complete — [N] emails processed, [N] digests read, [N] roles extracted, [N] written as Pending, [N] duplicates skipped, [N] rejected."

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

---

## Post-Scan Inbox Cleanup

After delivering scan results, produce a cleanup list of all digest emails that were scanned.
Format as a simple list:

**Emails to move to Job search 2026/Scanned (manual):**
- [sender] | [subject] | [date]

Non-digest emails (LinkedIn connection acceptances, messages, news) do not appear on this list.
Only job alert digests. Present the list at the end of every scan so the user can move them
in one pass.

## Session Logging

At scan start, write to event_log:
- event_type: "scan_start"
- event_detail: label scanned, number of emails retrieved
- result: "pass"

At scan completion, write to scan_metrics:
- emails_processed, roles_extracted, pending_written, duplicates_skipped,
  rejected, duration_seconds

At DB write completion, write to event_log:
- event_type: "db_write"
- event_detail: "Routed insert through integrity.py — result: [APPROVED/DUPLICATE/REJECTED]"
- result: "pass" or "fail"

If any email classification produces unexpected results, write to quality_flags:
- flag_type: "hallucination"
- severity: "medium"
