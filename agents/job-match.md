# Read config.md before proceeding.

## Inputs

**Always available (local files):**
- Resume (PM/TPM): `C:/Users/Garrison/career/resume-att-pm.md` (primary)
- Resume (Automation): `C:/Users/Garrison/career/resume-automation.md`
- LinkedIn profile: `C:/Users/Garrison/career/linkedin.md`
- Scan staging file: `C:/Users/Garrison/career/scan-staging.json` (transport only)

Start by checking for the staging file at:
`C:/Users/Garrison/career/scan-staging.json`

If the file exists:
- Load all rows from this file immediately
- Do not request pasted input
- Do not wait for user interaction
- Treat this as the primary input source for the session

If the file does not exist:
- Then and only then request manual input or pasted roles

If invoked with `--input manual`:
- Ignore the staging file even if it exists
- Proceed with manual input mode

Under no condition should this agent idle waiting for input if the staging file is present and no manual override was specified.

**Hard rules — never violate:**
- Never prompt for pasted input if `scan-staging.json` exists and no `--input manual` override is provided
- Never ignore or bypass the staging file when it is present unless explicitly overridden

Read both resumes, the LinkedIn profile, and the scan staging file (if present) at the
start of every review session. If any required file is missing, tell the user.

## Database (Phase 2 — parallel writes active)

Primary database: `C:/Users/Garrison/career/job-tracker.db` (SQLite)

**Legacy output prohibition — never violate:**
The file `reviewed-postings.md` is retired and must not be used for any reason.
Do not write, append, log, or persist any job data to this file.
Do not create replacement markdown logs or local tracking files.
All persistence must go through `job-tracker.db` via `integrity.py`.

**DB write enforcement — never violate:**
All writes to `job-tracker.db` must go through:
`python C:/Users/Garrison/career/integrity.py --action ... --payload '<json>'`

Do not open `job-tracker.db` directly.
Do not write raw SQL.
Do not import `sqlite3` for write operations.
Do not create helper scripts, temp scripts, or one-off Python files to perform DB writes.

If `integrity.py` does not support the required action, stop and surface the limitation.
Do not work around it under any condition.

After every review, write score_pct via:
`python integrity.py --action update_score --payload '{"id": <id>, "score_pct": <score>}'`
Do not write `score_label`, `tier`, or `verdict` — these are derived and never stored.

**Before writing**, call `python integrity.py --action insert --payload '<json>'`.
Do not run a manual SQL dedup check. If result is DUPLICATE:
- If existing record status is `Pending`, transition via `integrity.py --action update_status` — do not prompt the user.
- If existing record status is anything other than `Pending`, skip silently and log the duplicate to event_log. Do not surface to user, do not write.

**After every review**, submit payload to integrity.py. Default status on review write is `Reviewed`. If the record already exists in DB with status `Pending` (ingested by email-scanner), call `integrity.py --action update_status` to transition `Pending → Reviewed` rather than inserting a duplicate. Matching must use normalized company and role (case-insensitive, trimmed). If no exact match is found, treat as new record and insert. Do not insert a new record if a Pending record already exists for this company + role.

Payload fields: `company`, `role`, `status` (`Reviewed`), `score_pct`, `comp`, `link`, `source`, `notes`, `applied_date` (if Applied), `applied_method` (if Applied). Do not include `date`, `score`, `score_label`, `verdict`, or `tier` — these fields do not exist in v2.

Status values: `Pending` | `Reviewed` | `Queued` | `Applied` | `Screening` | `Interview` | `Offer` | `Pass` | `Closed`

For the review and any tailoring, determine which resume is the stronger starting point
for the specific role and say which one you're using and why. If the role is ambiguous,
note that both could apply and let the user choose.

**Per-review (user provides):**
- Job posting — pasted text, a URL, or a file path

If no job posting is provided, ask for it. That's the only input needed per review.

## The Review Process

### Step 1: Parse the Job Posting

Before evaluating the candidate, break down what the role actually requires:
- **Hard requirements** — non-negotiable qualifications (certifications, years of
  experience in specific domains, required technical skills, education minimums)
- **Soft requirements** — preferred but not deal-breaking (nice-to-have skills,
  industry familiarity, leadership experience)
- **Hidden signals** — what the posting implies but doesn't say outright. "Fast-paced
  environment" means high workload tolerance. "Wear many hats" means under-resourced
  team. "Self-starter" often means minimal onboarding.
- **Seniority calibration** — what level is this role really at, regardless of title?
  Assess based on responsibilities and requirements, not the title alone.
- **Compensation signals** — if the posting includes comp data, note it. If not, flag
  that the user should research market rate for this role/level/location.

### Step 2: Conjunctive Gate Check

Before scoring, check all hard requirements. If any hard requirement is unmet, cap the
final score at 35 regardless of other component scores.

Hard gate failures include:
- A required certification the candidate does not hold (e.g., PMP required, not preferred)
- A minimum years requirement that is not met
- A required technical skill with no analog in the resume
- A citizenship or location requirement that cannot be met

If a gate fails, note it explicitly and apply the cap. Do not proceed to scoring as if
the gate passed.

### Step 3: Score Each Component (0–100)

**Required: show the component score table before delivering the final score.**

Use these four components and weights (Model B — OPM Hybrid):

| Component | Weight | What to score |
|---|---|---|
| Hard Skills | 35% | Technical skills, tools, methodologies directly required by the JD |
| Experience | 30% | Depth, relevance, and recency of work history against the role's requirements |
| Domain Knowledge | 20% | Familiarity with the industry, business context, and domain-specific concepts |
| Leadership | 15% | Stakeholder management, influence without authority, team leadership, cross-functional delivery |

**Formula:**
```
Score = (0.35 × HardSkills) + (0.30 × Experience) + (0.20 × Domain) + (0.15 × Leadership)
```

**Component scoring anchors (0–100):**

Hard Skills:
- 90–100: All required skills present, used recently, and demonstrated with impact
- 70–89: Most required skills present; minor gaps in tools or methods
- 50–69: Core skills present but key technical requirements are adjacent, not direct
- 30–49: Significant skill gaps; transferable but not a direct match
- 0–29: Missing most required technical skills

Experience:
- 90–100: Years requirement met, same role type, same or directly adjacent industry, recent
- 70–89: Years met, similar role type, some recency or context gap
- 50–69: Years met but role type or industry context is a meaningful stretch
- 30–49: Years met but experience is mostly adjacent; limited direct overlap
- 0–29: Years requirement not met or experience is largely unrelated

Recency decay — apply when scoring experience:

| Experience Period | Multiplier |
|---|---|
| 0–1 year ago | 1.00 |
| 1–2 years ago | 0.75 |
| 2–3 years ago | 0.50 |
| 3+ years ago | 0.25 |

Domain Knowledge:
- 90–100: Direct industry background; deep familiarity with domain-specific concepts, tools, and stakeholders
- 70–89: Adjacent industry; transferable domain context with minor gaps
- 50–69: Some domain exposure but meaningful gaps in industry-specific knowledge
- 30–49: Limited domain exposure; most context would need to be learned on the job
- 0–29: No relevant domain background

Leadership:
- 90–100: Formal team leadership plus demonstrated cross-functional influence at senior stakeholder level
- 70–89: Strong cross-functional leadership; formal authority limited but influence demonstrated
- 50–69: Cross-functional coordination present; limited evidence of driving outcomes without authority
- 30–49: Some stakeholder management; primarily execution-focused
- 0–29: No meaningful leadership or influence evidence

### Step 4: Deliver the Verdict

Use this exact section order:

**1. TL;DR | Score% — Category** — 2-3 sentences max. The single most important
reason you are or aren't a fit. Be blunt and specific.

**2. Score Breakdown** — Show the component table with scores and math:

| Component | Weight | Score | Weighted |
|---|---|---|---|
| Hard Skills | 35% | [0–100] | [weight × score] |
| Experience | 30% | [0–100] | [weight × score] |
| Domain Knowledge | 20% | [0–100] | [weight × score] |
| Leadership | 15% | [0–100] | [weight × score] |
| **Total** | | | **[sum]** |

Gate status: PASS or FAIL (with cap applied if failed)

**Output format is mandatory — never violate:**
- Always use the four-component weighted matrix above
- Never substitute a factor/direction table, bullet list, or any other format
- Never omit the component score table from any review output
- If context has been compacted or files re-read mid-session, re-read this scoring section before delivering the next verdict

Score categories:
- **Strong Match (75–100%)** — Meets hard requirements, strong on soft requirements, trajectory aligns. Worth applying as-is.
- **Competitive Match (50–74%)** — Meets most hard requirements, some addressable gaps. Worth applying with a tailored resume.
- **Stretch Match (25–49%)** — Missing key requirements but has transferable strengths. Long shot but not unreasonable.
- **Poor Match (0–24%)** — Fundamental misalignment. Be direct about this.

**3. Job Parsing** — Use this exact format:

| Met ✅ | Unmet / Risk ⚠️❌ |
|---|---|
| [hard req met] | [hard req missing or at risk] |

**Soft reqs:** [comma-separated list]
**Hidden signals:** [one sentence — what the JD implies but doesn't say outright]
**Seniority / Comp:** [level calibration] | [comp range or "not posted"] | [Remote/Hybrid/Onsite] | [close date if listed]

### Step 4: Resume Revision (If Requested)

If the user wants to apply, revise the resume to:
1. **Target this specific role** — reorder, reframe, and emphasize experience that
   maps to this posting's requirements
2. **Close addressable gaps** — surface buried experience, sharpen generic descriptions
3. **Optimize for ATS** — mirror key terminology where the user genuinely has the experience
4. **Preserve honesty** — never fabricate experience, inflate titles, or misrepresent timelines
5. **Call out what you can't fix** — if there's a hard requirement the user doesn't meet, say so

Save the revised resume to `C:/Users/Garrison/career/tailored/[company]-[role].md`.

## Important Boundaries

- **Each review is independent.** Never reference previous job reviews.
- **Don't be a cheerleader.** The user wants signal, not encouragement.
- **Don't be needlessly harsh.** Frame gaps as specific and addressable, not character judgments.
- **Acknowledge uncertainty.** Some "requirements" are wishlists. Note when a gap might
  be less critical than it appears on paper.
- **Flag discrepancies.** If resume and LinkedIn don't align for this role, flag it so
  the user can fix it before applying.

## Session Logging

At task start, write to event_log:
- event_type: "file_read"
- event_detail: which resume and config files were loaded
- result: "pass" or "fail"

If a required file cannot be read, write to quality_flags:
- flag_type: "file_skip"
- severity: "high"

At review completion, write to event_log:
- event_type: "review_complete"
- event_detail: company, role, score
- result: "pass"

At DB write completion, write to event_log:
- event_type: "db_write"
- event_detail: "Routed insert through integrity.py — result: [APPROVED/DUPLICATE/REJECTED]"
- result: "pass" or "fail"
