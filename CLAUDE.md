# CLAUDE.md — Session Rules
# Loaded automatically. These rules apply to every session, every agent, every task.
## File Reading — Mandatory
Before taking any action, read the relevant agent file and confirm it is loaded.
Echo back the section headers as confirmation. Do not proceed until this is done.
Agent files are at:
- `C:/Users/<username>/career/agents/sera.md`
- `C:/Users/<username>/career/agents/job-match.md`
- `C:/Users/<username>/career/agents/email-scanner.md`
- `C:/Users/<username>/career/agents/job-log.md`
- `C:/Users/<username>/career/config.md`
If a file cannot be read, stop and tell the user. Do not proceed from memory.
## Execution Rules
**Do not design when asked to build.** If the user asks for code, write code.
If the user asks for a file edit, make the edit. Do not produce a design document
unless explicitly asked for one.
**Do not ask clarifying questions when the intent is clear.** Make a reasonable
assumption, state it in one sentence, and proceed.
**When corrected, acknowledge in one sentence and move forward.** Do not re-litigate,
do not over-explain, do not apologize at length.
**If you drift long, self-correct.** Tighten the response and continue.
## Gmail Rules
Email operations use the Gmail MCP tools only:
- `gmail_search_messages`
- `gmail_read_message`
- `gmail_list_labels`
- `gmail_get_profile`
Do NOT access project cache files directly. Paths like
`C:/Users/<username>/.claude/projects/*/tool-results/*` are off limits.
Do not read, parse, or execute against these files under any circumstances.
The Gmail MCP does not support label modification. Do not attempt to move emails
programmatically. After every email scan, produce a manual cleanup list in this format:
**Emails to move to Job search 2026/Scanned (manual):**
- [sender] | [subject] | [date]
## Agent Logging
After every task, write session data to `C:/Users/<username>/career/monitor.db`.
Follow the logging instructions in the relevant agent file exactly.
Do not skip logging steps.
## Hard Stops
- Never access `C:/Users/<username>/.claude/projects/*/tool-results/*`
- Never attempt Gmail label modification via any method
- Never proceed without reading the relevant agent file first
- Never fabricate resume content, job history, or application data
