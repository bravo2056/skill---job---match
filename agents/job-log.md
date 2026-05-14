# Read config.md before proceeding.

## Job Search Log

The user maintains a NJ Unemployment Insurance work search log at:
`C:/Users/Garrison/career/job-search-log.csv`

**An apply action is not complete unless the CSV row is written AND verified.**
Reporting "applied" without CSV verification is a hard failure — it has caused 26+ silent drops on the legal NJDOL log. Never claim success without the verification step below.

### Required sequence on every "mark applied" / "applied" action

1. Update DB through the existing approved path (integrity.py update_status / write_review).
2. Write the CSV row with the columns listed below.
3. Re-read `job-search-log.csv` from disk.
4. Confirm a row exists with matching Employer Name + Position Applied For + Date of Contact.
5. Only then report success to the user.

If any of steps 2-4 fail, report failure clearly. Do not say the apply was completed. Write a `quality_flags` entry (flag_type "csv_verify_fail", severity "high") via integrity.py and surface the gap to the user.

**When an application is confirmed** (user says "applied", "mark applied", or similar), immediately add an entry to the CSV — no permission prompt needed. This log is legally required for NJ UI compliance and must always be kept current. Columns to write:
- Week Starting (Sunday of the certification week)
- Date of Contact
- Time of Contact (timestamp required for audit compliance — use format 10:00 AM)
- Employer Name
- Address / URL
- Phone (use email if no phone available)
- Method of Contact
- Position Applied For
- Person Contacted
- Application Taken? (Yes/No)
- Result

Always read the file before editing to avoid overwriting existing entries.

**When the user asks to see or export the log**, read the file and display it as a
formatted markdown table so it's easy to read in the conversation.

## Session Logging

At CSV write completion, write to event_log:
- event_type: "csv_write"
- event_detail: employer name, role, date logged
- result: "pass" or "fail"

If the CSV write fails or the file cannot be read before editing, write to quality_flags:
- flag_type: "file_skip"
- severity: "high"
