# Read config.md before proceeding.

## Job Search Log

The user maintains a [STATE] Unemployment Insurance work search log at:
`C:/Users/<username>/career/job-search-log.csv`

**When an application is confirmed** (user says "applied", "mark applied", or similar), immediately add an entry to the CSV — no permission prompt needed. This log must always be kept current. Columns to write:
- Week Starting (Sunday of the certification week)
- Date of Contact
- Time of Contact (timestamp required — use format 10:00 AM)
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
