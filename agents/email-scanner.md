# Read config.md before proceeding.

## Email Scanning (Gmail — "Job search 2026" label)

When the user asks to scan emails, retrieve ALL emails in the "Job search 2026" label (do not
filter by sender upfront). Then classify each email:
- **Job digest / alert** (Jobright, HiringCafe, LinkedIn Job Alerts, ZipRecruiter, Indeed, etc.) — scan and score
- **Application confirmations, recruiter messages, personal emails** — leave in place, do not scan

This label-first approach ensures job alerts from any source (Indeed, Dice, Glassdoor, etc.) are
caught regardless of sender. Extract all roles, evaluate each against the resume, and present
results in three tiers:

### Tier 1 — 65%+ (Apply Candidates)
Full detail for each:
- Company | Role | Score | Comp | Location/Remote
- Direct JD link (resolve tracking URLs from email if needed)
- 2-sentence reason it cleared

### Tier 2 — 50–64% (Borderline)
Brief entry for each:
- Company | Role | Score | Comp | Location/Remote | Link
- One-line reason it didn't clear 65%

### Tier 3 — Below 50% (Filtered)
Condensed list only — no links unless user asks:
- Company | Role | Score | One-line reason filtered

### Auto-filter (never surface, log as Pass):
- PMP as a hard filter (not preferred)
- Onsite outside commute range (~45 min from [HOME_LOCATION])
- Comp ceiling under [COMP_FLOOR] — filter only when the ceiling of the posted range is under [COMP_FLOOR]; a low floor alone is not a disqualifier
- Underleveled roles (less than 5 years experience required)
- Non-target roles: design, sales, developer relations, evangelist, marketing, HR
- Pure hands-on engineering roles (software dev, network engineer, hardware, manufacturing/chemical process engineering)
- **Note:** Two valid target tracks exist — evaluate against BOTH before filtering:
  - **Track 1 (PM resume):** TPM, Technical Program Manager, Senior PM, Director of Programs
  - **Track 2 (Automation resume):** Process Engineer, Business Process Analyst, Operations Automation, Workflow Engineer, Systems Operations Manager, Continuous Improvement Manager — roles centered on designing/optimizing operational workflows and automation systems
- **Placement restrictions (hard stop)** — check config.md for any active placement restrictions. Any role matching a restriction should be flagged and passed regardless of fit. Note the restriction in the verdict; do not surface as a candidate.

### Surface with a flag (don't auto-filter):
- Domain gap is "preferred" not "required" — note the gap, still surface
- Domain outside core background (fintech/payments, healthcare IT, construction, aerospace,
  consumer hardware/firmware, data center hardware ops, biotech/pharma, real estate/mortgage,
  advertising/media agency) — surface in Tier 2 or Tier 3 based on score, flag the domain gap
- Remote status unclear — flag it
- Comp not posted — flag it, still surface if role otherwise fits

After scanning, check the SQLite database (`job-tracker.db`) for any role already logged — query by company + role. Also cross-check reviewed-postings.md as a fallback during Phase 2.

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
- emails_processed, tier1_count, tier2_count, tier3_count,
  auto_filtered, previously_reviewed, duration_seconds

At DB write completion, write to event_log:
- event_type: "db_write"
- event_detail: "job-tracker.db — reviewed_postings"
- result: "pass" or "fail"

If any email classification produces unexpected results, write to quality_flags:
- flag_type: "hallucination"
- severity: "medium"
