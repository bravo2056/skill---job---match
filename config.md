# Job Search Config

Single source of truth for all subagents. Read this file at startup before doing anything else.

---

## File Paths

| File | Path |
|------|------|
| Resume (PM/TPM) | `C:/Users/Garrison/career/resume-att-pm.md` |
| Resume (Automation) | `C:/Users/Garrison/career/resume-automation.md` |
| LinkedIn profile | `C:/Users/Garrison/career/linkedin.md` |
| Job search log (CSV) | `C:/Users/Garrison/career/job-search-log.csv` |
| Reviewed postings log | `C:/Users/Garrison/career/reviewed-postings.md` |

---

## SQLite Database

Primary database: `C:/Users/Garrison/career/job-tracker.db`
Backup flat file: `C:/Users/Garrison/career/reviewed-postings.md` (read-only backup, keep in sync)

**Read pattern** — check for duplicate before delivering a review:
```python
import sqlite3
conn = sqlite3.connect(r"C:/Users/Garrison/career/job-tracker.db")
cur = conn.cursor()
cur.execute("SELECT date, score, verdict, status FROM reviewed_postings WHERE company=? AND role=?", (company, role))
row = cur.fetchone()
conn.close()
```

**Write pattern** — run after every review:
```python
import sqlite3
conn = sqlite3.connect(r"C:/Users/Garrison/career/job-tracker.db")
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

Filter only when the **ceiling** of the posted range is under $130K. A low floor alone is not a disqualifier.

---

## Verizon Hard Stop

Severance agreement prohibits working for Verizon until **August 20, 2026**. Any role where the client is Verizon or likely Verizon must be flagged and passed regardless of fit.

**Location indicators:**
- Basking Ridge, NJ
- Bedminster, NJ
- Branchburg, NJ
- Postings referencing "major telecom client NJ" through a staffing firm

Note the restriction in the verdict; do not surface as a candidate.

---

## NJ Commute Range

Onsite roles must be within approximately **45 minutes of Hillsborough, NJ**. Filter any onsite role outside this range.

---

## Domain Scoring

Domain gaps are handled by the scoring rubric (20% weight on Domain Knowledge component) — not by auto-filtering. All domains proceed to scoring. A role in biotech, real estate, or any other unfamiliar domain will score low on Domain Knowledge and land in Stretch or Poor naturally.

**Note:** Government/public sector roles are NOT filtered — evaluate on fit.

**Domain gap scoring note:** Domain gaps lower the score through the Domain Knowledge component. Never surface domain gaps as separate warning flags. The score speaks for itself.

**Additional auto-filter conditions:**
- PMP as a hard requirement (not preferred)
- Comp ceiling under $130K
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
