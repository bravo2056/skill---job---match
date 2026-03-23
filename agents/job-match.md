# Read config.md before proceeding.

## Inputs

**Always available (local files):**
- Resume (PM/TPM): `C:/Users/<username>/career/resume-att-pm.md` (primary)
- Resume (Automation): `C:/Users/<username>/career/resume-automation.md`
- LinkedIn profile: `C:/Users/<username>/career/linkedin.md`
- Job search log: `C:/Users/<username>/career/job-search-log.csv`
- Reviewed postings log: `C:/Users/<username>/career/reviewed-postings.md`

Read both resumes, the LinkedIn profile, and the reviewed postings log at the start of
every review. If any file is missing, tell the user and ask them to paste the content
so you can save it there.

## Database (Phase 2 — parallel writes active)

Primary database: `C:/Users/<username>/career/job-tracker.db` (SQLite)
Backup flat file: `C:/Users/<username>/career/reviewed-postings.md` (read-only backup, keep in sync)

**Before delivering a review**, check for a matching company + role by running:
```python
import sqlite3
conn = sqlite3.connect(r"C:/Users/<username>/career/job-tracker.db")
cur = conn.cursor()
cur.execute("SELECT date, score, verdict, status FROM reviewed_postings WHERE company=? AND role=?", (company, role))
row = cur.fetchone()
conn.close()
```
If a match is found, flag it immediately: "This posting looks like one reviewed on [date] — verdict was [verdict]. Want to re-review or skip it?"

**After every review**, write to BOTH the database AND the markdown file:

Database insert:
```python
import sqlite3
conn = sqlite3.connect(r"C:/Users/<username>/career/job-tracker.db")
cur = conn.cursor()
cur.execute("""
    INSERT INTO reviewed_postings (date, company, role, score, score_pct, verdict, status, comp, remote, link, notes)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
""", (date, company, role, score_label, score_int, verdict, status, comp, remote, link, notes))
conn.commit()
conn.close()
```

Markdown append (same as before — append a row to reviewed-postings.md):
`| date | company | role | score | verdict |`

Status values: `Pending` | `Applied` | `Borderline` | `Pass` | `Reviewing`

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

### Step 2: Assess the Candidate

Review the resume and LinkedIn profile against the parsed posting. Look for:

**Alignment:**
- Direct experience matches (same skills, same industry, same level)
- Adjacent experience (transferable skills, related domains)
- Trajectory fit (is your career arc pointing toward this role?)

**Gaps:**
- Missing hard requirements (potential deal-breakers)
- Missing soft requirements (addressable but worth noting)
- Experience level mismatches (over-qualified is a gap too)
- Recency issues (had the skill 10 years ago but not recently)

**Red flags a recruiter would notice:**
- Employment gaps without explanation
- Job hopping patterns
- Title regression
- Mismatches between resume and LinkedIn
- Generic descriptions that don't show impact

**Strengths a recruiter would notice:**
- Quantified achievements
- Progression within companies
- Relevant certifications or patents
- Industry recognition or thought leadership

### Step 3: Deliver the Verdict

Use this exact section order:

**1. TL;DR** — 2-3 sentences max. Lead with the match score and category, then the
single most important reason you are or aren't a fit. This is the first thing the
user sees so make it count — be blunt and specific.

**2. Match Score** — Present as a table with two columns: Factor and Direction
(✅ Helping / ❌ Hurting). List exactly 3 helping factors and 3 hurting factors.
Categories:
- **Strong Match (75-100%)** — Meets hard requirements, strong on soft requirements,
  trajectory aligns. Worth applying as-is.
- **Competitive Match (50-74%)** — Meets most hard requirements, some addressable gaps.
  Worth applying with a tailored resume.
- **Stretch Match (25-49%)** — Missing key requirements but has transferable strengths.
  Long shot but not unreasonable.
- **Poor Match (0-24%)** — Fundamental misalignment. Be direct about this.

**3. Job Parsing** — Break down what the role actually requires:
- Hard requirements, soft requirements, hidden signals, seniority calibration,
  compensation signals (as described in Step 1 above)

### Step 4: Resume Revision (If Requested)

If the user wants to apply, revise the resume to:
1. **Target this specific role** — reorder, reframe, and emphasize experience that
   maps to this posting's requirements
2. **Close addressable gaps** — surface buried experience, sharpen generic descriptions
3. **Optimize for ATS** — mirror key terminology where the user genuinely has the experience
4. **Preserve honesty** — never fabricate experience, inflate titles, or misrepresent timelines
5. **Call out what you can't fix** — if there's a hard requirement the user doesn't meet, say so

Save the revised resume to `C:/Users/<username>/career/tailored/[company]-[role].md`.

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
- event_detail: "job-tracker.db — reviewed_postings"
- result: "pass" or "fail"
