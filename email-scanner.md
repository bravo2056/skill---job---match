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

Format for each role:
`Company | Role | Score% | Comp | Location/Remote | Link`
✅ Why it cleared — one sentence.
⚠️ Biggest risk — one sentence.

### Tier 2 — 50–64% (Borderline)

Format for each role:
`Company | Role | Score% | Comp | Location/Remote`
↳ One-liner: what's holding it below 65%.

### Tier 3 — Below 50% / Filtered / Gate Fails

Single flat list — no links unless user asks. Auto-filters, gate fails, and poor scores all go here:
`Company | Role | Score% | One-line reason`

### Auto-filter (never surface, log as Pass):
- PMP as a hard filter (not preferred)
- Onsite outside NJ commute range (~45 min from Hillsborough NJ)
- Comp ceiling under $130K — filter only when the ceiling of the posted range is under $130K; a low floor alone is not a disqualifier
- Underleveled roles (less than 5 years experience required)
- Non-target roles: design, sales, developer relations, evangelist, marketing, HR
- Pure hands-on engineering roles (software dev, network engineer, hardware, manufacturing/chemical process engineering)
- **Note:** Two valid target tracks exist — evaluate against BOTH before filtering:
  - **Track 1 (PM resume):** TPM, Technical Program Manager, Senior PM, Director of Programs
  - **Track 2 (Automation resume):** Process Engineer, Business Process Analyst, Operations Automation, Workflow Engineer, Systems Operations Manager, Continuous Improvement Manager — roles centered on designing/optimizing operational workflows and automation systems
- **Verizon placements (hard stop through 20 Aug 2026)** — severance agreement prohibits
  working for Verizon until August 20, 2026. Any role where the client is Verizon or likely
  Verizon should be flagged and passed regardless of fit. Indicators: Basking Ridge NJ,
  Bedminster NJ, Branchburg NJ addresses, or postings that reference "major telecom client NJ"
  through a staffing firm. Note the restriction in the verdict; do not surface as a candidate.

### Surface with a flag (don't auto-filter):
- Domain gap is "preferred" not "required" — note the gap, still surface
- All domains proceed to scoring — domain gaps are handled by the 20% Domain Knowledge weight in the rubric, not by filtering. Score determines tier.
- Remote status unclear — flag it
- Comp not posted — flag it, still surface if role otherwise fits

After scanning, check the SQLite database (`job-tracker.db`) for any role already logged — query by company + role. Also cross-check reviewed-postings.md as a fallback during Phase 2.

### Staffing Agency Duplicate Detection

When a posting comes from a staffing agency (PEAK, Insight Global, Aditi, TalentBurst, Intelliswift, Kelly, Robert Half, TEKsystems, etc.), extract the underlying employer name from the job description and check that name against the DB separately. Surface the result to the user before scoring:

> **Staffing agency posting detected** — underlying employer appears to be [Employer]. DB check: [match found / no match]. Proceed?

Do not auto-score until the user confirms it is not a duplicate.

## Context Window Management

Email scans are context-heavy. Each email body (especially LinkedIn and ZipRecruiter digests at 90–200KB) consumes a large portion of the context window. To avoid timing out mid-scan:

**Required protocol — run `/compact` between Phase 1 and Phase 2:**
- **Phase 1:** Read all emails, collect all role data, run DB duplicate checks, surface staffing agency confirmations. Do NOT score yet.
- **`/compact`** — run this after Phase 1 is complete. This compresses the conversation and frees the context window before the scoring pass begins.
- **Phase 2:** Score all confirmed roles, deliver Tier 1 / Tier 2 / Tier 3 results, write to DB, log metrics, produce cleanup list.

If the user does not manually run `/compact`, prompt them:
> "Phase 1 complete — all emails read, [N] roles collected. Run `/compact` now before I start scoring to avoid a context timeout."

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