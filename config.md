# Job Search Config

Single source of truth for all subagents. Read this file at startup before doing anything else.

---

## File Paths

| File | Path |
|------|------|
| Resume (PM/TPM) | `C:/Users/<username>/career/resume-att-pm.md` |
| Resume (Automation) | `C:/Users/<username>/career/resume-automation.md` |
| LinkedIn profile | `C:/Users/<username>/career/linkedin.md` |
| Job search log (CSV) | `C:/Users/<username>/career/job-search-log.csv` |
| Reviewed postings log | `C:/Users/<username>/career/reviewed-postings.md` |

---

## SQLite Database

Primary database: `C:/Users/<username>/career/job-tracker.db`
Backup flat file: `C:/Users/<username>/career/reviewed-postings.md` (read-only backup, keep in sync)

**Read pattern** — check for duplicate before delivering a review:
```python
import sqlite3
conn = sqlite3.connect(r"C:/Users/<username>/career/job-tracker.db")
cur = conn.cursor()
cur.execute("SELECT date, score, verdict, status FROM reviewed_postings WHERE company=? AND role=?", (company, role))
row = cur.fetchone()
conn.close()
```

**Write pattern** — run after every review:
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

Status values: `Pending` | `Applied` | `Borderline` | `Pass` | `Reviewing`

---

## Score Categories

| Category | Range | Meaning |
|----------|-------|---------|
| Strong Match | 75–100% | Meets hard requirements, strong on soft requirements, trajectory aligns. Worth applying as-is. |
| Competitive Match | 50–74% | Meets most hard requirements, some addressable gaps. Worth applying with a tailored resume. |
| Stretch Match | 25–49% | Missing key requirements but has transferable strengths. Long shot but not unreasonable. |
| Poor Match | 0–24% | Fundamental misalignment. |

---

## Comp Floor

Filter only when the **ceiling** of the posted range is under [COMP_FLOOR]. A low floor alone is not a disqualifier.

---

## PLACEMENT_RESTRICTIONS
# Add any placement restrictions here:
# - Employer name
# - End date
# - Location indicators (city, address patterns)
# - Staffing firm language patterns

---

## Commute Range

Onsite roles must be within approximately **45 minutes of [HOME_LOCATION]**. Filter any onsite role outside this range.

---

## Auto-Filter Domains

Never surface these — log as Pass:

- Fintech / payments
- Healthcare IT
- Construction
- Aerospace
- Consumer hardware / firmware
- Data center hardware ops
- Biotech / pharma
- Real estate / mortgage
- Advertising / media agency

**Note:** Government/public sector roles are NOT filtered — evaluate on fit.

**Additional auto-filter conditions:**
- PMP as a hard requirement (not preferred)
- Comp ceiling under [COMP_FLOOR]
- Underleveled roles (less than 5 years experience required)
- Non-target roles: design, sales, developer relations, evangelist, marketing, HR
- Pure hands-on engineering: software dev, network engineer, hardware, manufacturing/chemical process engineering

---

## Target Role Tracks

Evaluate against BOTH tracks before filtering.

**Track 1 — PM resume (`resume-att-pm.md`):**
TPM, Technical Program Manager, Senior PM, Director of Programs

**Track 2 — Automation resume (`resume-automation.md`):**
Process Engineer, Business Process Analyst, Operations Automation, Workflow Engineer, Systems Operations Manager, Continuous Improvement Manager — roles centered on designing/optimizing operational workflows and automation systems
