# Read config.md before proceeding.

## Email Scanning (Gmail — "Job search 2026" label)

The scan is now script-orchestrated. Phase 1 runs as `python run-scan.py`
(invoked from `sera-scan.bat`). The agent's job is Phase 2 only:
verify Phase 1 output, gate the user, ingest from staging, close any
matched rejections, and relabel processed threads.

### Phase 1 — Verify (script-driven)

The script `C:/Users/Garrison/career/run-scan.py` does everything:

- Authenticates with Gmail via OAuth (token at `token.json`)
- Lists threads under label "Job search 2026"
- Routes each via `email-scanner.py route_thread()`:
  - subject contains a canonical rejection phrase → `rejection`
  - sender has a configured parser in `PARSERS` → `digest`
  - otherwise → `skip`
- Per-sender deterministic parsers extract rows. Per-sender parser specs
  (URL patterns, anchor rules, JD cap) live in `email-scanner.py`
  docstrings — read those before modifying parsers.
- Writes:
  - `C:/Users/Garrison/career/scan-staging.json` (digest rows)
  - `C:/Users/Garrison/career/rejection-staging.json` (rejection candidates with `company:null`, `role_title:null`)
  - `C:/Users/Garrison/career/unparsed-staging.json` (no-parser senders)
  - `C:/Users/Garrison/career/scan-staging.json.complete` (sentinel)
- Logs to `monitor.db.event_log` via `integrity.py event_log_write`

**Agent steps:**

**Step 0 — Decision protocol (run BEFORE any tool call).**

On any "scan email" / "run scan" / "scan inbox" trigger:

1. Confirm you have read this file in full. Echo back exactly:
   `Loaded email-scanner-v2.md.`
2. Check for `C:/Users/Garrison/career/scan-staging.json.complete`.
3. Echo your decision in one line BEFORE any further tool call:
   - **Sentinel present** → `Sentinel present → proceeding with Phase 2 (verify staging + ingest).` Skip to Step 1.
   - **Sentinel absent** → `Sentinel absent → running Phase 1 via run-scan.py.` Then run Phase 1 yourself (substeps below). After it completes, proceed to Step 1.

When sentinel absent, run Phase 1:

a. Launch dashboard if not up (idempotent):
   ```
   C:/Users/Garrison/career/launch-dashboard.bat
   ```

b. Run the script:
   ```
   python C:/Users/Garrison/career/run-scan.py
   ```

c. Verify the sentinel now exists. If still absent → script crashed. Stop and report the script's output. Do NOT retry automatically.

d. Echo `Phase 1 complete → proceeding to Phase 2.` Then continue to Step 1.

Required, not optional. The echoed decision is the forced narration that
prevents drift back to the old MCP-based scan flow. Use `python run-scan.py`
for the bulk fetch — never call `get_thread` (Gmail MCP) for a digest body.

The sentinel is destroyed at the end of every successful Phase 2 (Step 8),
so a present sentinel always means "Phase 1 just ran." No mtime check needed.

Token caveat: Google's OAuth "Testing" mode forces a re-consent every ~7 days.
If `run-scan.py` errors with `invalid_grant` or `RefreshError`, the token
expired. Tell the user: "Token expired — please run `python run-scan.py`
once interactively to re-grant consent, then trigger again."

**Step 1:** Read counts (do not load full bodies into context).

Use a python one-liner:
```
python -c "import json; print(len(json.load(open('scan-staging.json'))), len(json.load(open('rejection-staging.json'))), len(json.load(open('unparsed-staging.json'))))"
```

Never read `scan-staging.json` directly with the Read tool — bodies + URLs
are too large for context and force chunked reads.

**Step 2:** Backlog warning protocol.

If total roles in scan-staging.json > 150, pause and output exactly:

> "Extraction paused — [N] roles collected from [N] emails. This is a
> large backlog. Choose: (1) continue and score all, (2) score only roles
> from emails received in the last 7 days, (3) stop here and save Phase 1
> extraction for staged scoring later, or (4) restart with a narrower
> date range."

Do not continue until the user selects an option. Do not infer a preference.

**Step 3:** Fill in rejection company + role from subject.

For each row in `rejection-staging.json` with `company:null`, parse the
subject:

- "Your application for <role> at <company>" → role, company
- "Update on your application for <role>" → role; company from sender domain
- "[role] at [company]" → as-is
- Pattern doesn't match → leave as empty strings; bulk_resolve returns NOT_FOUND → match_type="unmatched"

Write the parsed values back to `rejection-staging.json`.

**Step 4:** Run rejection bulk_resolve.

```
python integrity.py --action bulk_resolve --payload '{"items":[{"company":"X","role":"Y"}, ...]}'
```

For each result, update the corresponding row in `rejection-staging.json`
with `resolved_id`, `prior_status`, and `match_type` per the rules below.

Resolution handling — every rejection is recorded; `match_type` controls
whether Phase 2 closes:

- **Exact match, prior_status in Applied / Screening / Interview / Offer** → `match_type:"exact"`. Phase 2 closes.
- **Exact match, prior_status in Pending / Reviewed / Queued (never applied)** → `match_type:"not_applied"`. Phase 2 does NOT close. Log immediately: `event_type:"rejection_not_applied"`, `event_detail:"<sender> | <subject> | id=<resolved_id>, prior_status=<status>"`.
- **Exact match, prior_status terminal (Pass or Closed)** → `match_type:"already_terminal"`. Phase 2 does NOT close.
- **Fuzzy match, single unambiguous candidate, prior_status in Applied/Screening/Interview/Offer** → `match_type:"fuzzy"`. Phase 2 closes.
- **Fuzzy match, single unambiguous candidate, prior_status in Pending/Reviewed/Queued** → `match_type:"not_applied"`.
- **Fuzzy match, single unambiguous candidate, prior_status terminal** → `match_type:"already_terminal"`.
- **Fuzzy match, multiple candidates** OR **no match** → `resolved_id:null`, `match_type:"unmatched"`. Log: `event_type:"rejection_unmatched"`, `event_detail:"<sender> | <subject>"`.

Applied+ status set: `{Applied, Screening, Interview, Offer}`. Closed and Pass
are terminal → `already_terminal`. Pending/Reviewed/Queued → `not_applied`.

Never auto-create a new DB row from a rejection email. Never modify a row from
Phase 1; close happens only in Phase 2 after explicit confirmation.

**Step 5:** Output Phase 1 verification notice.

> "Phase 1 verified — [N] roles staged, [N] rejection candidates ([N] exact, [N] fuzzy, [N] not_applied, [N] already_terminal, [N] unmatched), [N] unparsed senders. Awaiting confirmation to begin Phase 2."

Do not begin Phase 2 until the user explicitly confirms.

---

### Phase 2 — Write and Close

Only after explicit user confirmation:

**Step 1:** Pass `scan-staging.json` straight to `ingest_batch`.

No transform step. integrity.py auto-aliases the long names
(`source_email→source`, `role_title→role`, `canonical_link→link`) and silently
ignores `jd_excerpt`, `thread_id`, and any other staging-only fields. The alias
bridge is documented at `integrity.py handle_ingest()`.

```
python integrity.py --action ingest_batch --payload-file scan-staging.json > ingest-results.json
```

Result codes per row: APPROVED (Pending), AUTO-PASS (Pass), DUPLICATE,
REJECTED (schema). Surface counts to the user.

**Step 2:** Log Phase 2 ingest results.

Write a single `db_write` summary to `event_log` with totals. Plus one
`event_log` entry per non-DUPLICATE row (APPROVED, AUTO-PASS, REJECTED) for
audit traceability. DUPLICATEs roll up into the summary only.

NJ-compliance audit must be able to answer "what got created on date X" by
querying `event_log` directly.

**Step 3:** Close matched rejections.

For each rejection in `rejection-staging.json` with `match_type` of `exact`
or `fuzzy`, call:

```
python integrity.py --action update_status --payload '{"id":<resolved_id>,"status":"Closed","notes":"Rejection received <received_date> — <sender>"}'
```

Log each via `event_log`:
- `event_type: rejection_close`
- `event_detail: id=<id>, prior_status=<prior_status>, received=<received_date>, match_type=<exact|fuzzy>`
- `result: pass` or `fail`

If integrity.py returns an error (e.g., row already in terminal state due to a
race), log the failure with `result:"fail"` and continue. Do not retry, do
not force-update.

Rows with `match_type` of `not_applied`, `already_terminal`, or `unmatched`
are NOT written. They were already logged in Phase 1.

**Step 4:** Auto-relabel processed threads.

Use the existing OAuth token (`token.json`) to call Gmail API. Move threads
from `Job search 2026` → `Job search 2026/Scanned` per these rules:

| Bucket | Relabel iff |
|---|---|
| Digest thread | rows attempted by `ingest_batch` AND ≥1 row APPROVED or DUPLICATE |
| Rejection thread | `match_type ∈ {exact, fuzzy}` AND `prior_status ∈ {Applied, Screening, Interview, Offer}` |
| Unparsed thread | never |

Never relabel: 100% REJECTED digest threads (parser glitches), threads not
attempted (e.g., backlog option 3), `not_applied` rejections,
`already_terminal` rejections, `unmatched` rejections.

Net rule: a thread relabels iff the action we said we'd take actually
executed against the DB. Broken-or-unprocessed stuff stays visible.

Implementation: each staging row carries `thread_id` (stamped post-parse by
`scan_digest()`). Group `ingest-results.json` by row index → look up
`scan-staging.json[i]['thread_id']` → apply the relabel rule per thread →
batch the Gmail API calls.

**Step 5:** Confirm dashboard is running.

`launch-dashboard.bat` is idempotent. Run if not already up. Log completion.

**Step 6:** Output scan completion summary.

> "Scan complete — [N] emails processed, [N] digests read, [N] roles extracted, [N] staged as Pending, [N] auto-passed (filtered), [N] duplicates skipped, [N] rejected (schema). Rejections: [N] closed, [N] not applied, [N] already terminal, [N] unmatched. [N] threads relabeled to Scanned."

**Step 7:** Surface unparsed-staging.json.

List the unparsed senders + subjects. Recommend either (a) skipping (delete
the record from staging) or (b) queuing parser work for that sender. These
threads remain in `Job search 2026` for manual review.

**Step 8:** Delete the sentinel.

Required, not optional. Run:

```
rm C:/Users/Garrison/career/scan-staging.json.complete
```

(Or `del` on Windows cmd, or unlink via Python — any path that removes the
file works.)

This invalidates the current staging set. The next "scan email" trigger will
see no sentinel and route the user to `sera-scan.bat` for a fresh Phase 1,
preventing a stale staging file from being re-ingested.

If Phase 2 fails partway (e.g., ingest_batch errors out mid-batch), do NOT
delete the sentinel. Tell the user what failed; leave the sentinel so they
can retry Phase 2 against the same staging without re-scanning.

---

### Hard rules — never violate

- Never call `get_thread` or any Gmail MCP tool from this skill. The script owns all body fetches via Gmail API direct.
- Never read `scan-staging.json` with the Read tool. Use a python one-liner to count or filter — bodies + URLs are too large for context.
- Always use `ingest_batch --payload-file` for Phase 2 inserts. Per-row `ingest` calls trigger a permission prompt per payload and are slow.
- Never auto-create a new DB row from a rejection email.
- Never re-open or change status of a row already in Pass or Closed via a rejection.
- Never modify any field other than `status` and `notes` from a rejection.
- Never close a row from a fuzzy match with multiple candidates — log as unmatched.
- Never close a row whose prior_status is not in {Applied, Screening, Interview, Offer} — log as `not_applied` instead. Do not synthesize an applied_date to bypass the integrity check.
- Never recover extracted data from JSONL session logs or transcript files.
- Never dump raw email bodies to disk for any reason.
- If Phase 2 context is lost mid-ingest, stop and tell the user — do not attempt recovery from partial ingest-results.json.
- Never write `score`, `score_pct`, `score_label`, `verdict`, or `tier` to DB from this skill.
- Never continue past the backlog warning without an explicit user selection.

---

### Session Logging

All event_log writes route through:
```
python integrity.py --action event_log_write --payload '{"agent_name":"email-scanner","session_id":"<scan_session_id>","event_type":"<type>","event_detail":"<detail>","result":"pass|fail"}'
```

Direct sqlite3 writes to monitor.db are not used for event_log entries.
`scan_metrics` is written directly to monitor.db (no integrity.py wrapper for metrics).

The script writes `scan_start`, `digest_processed`, `rejection_staged`,
`unparsed_staged`, `scan_complete` automatically.

The agent writes:
- `db_write` (summary, Phase 2 Step 2)
- per-row events for APPROVED / AUTO-PASS / REJECTED (Phase 2 Step 2)
- `rejection_close` per close (Phase 2 Step 3)
- `rejection_not_applied` per not_applied case (Phase 1 Step 4)
- `rejection_unmatched` per unmatched case (Phase 1 Step 4)
- `inbox_relabel` summary per relabel batch (Phase 2 Step 4)

At scan completion, INSERT one row into `scan_metrics` with these columns
(others left NULL):
- session_id, date, emails_processed, duration_seconds
- roles_extracted, pending_written, duplicates_skipped, rejected
- rejections_closed, rejections_not_applied, rejections_already_terminal, rejections_unmatched

If any email classification produces unexpected results, write to
`quality_flags` via `integrity.py write_flag`:
- flag_type: "hallucination"
- severity: "medium"
