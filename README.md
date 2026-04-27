# Job Search Agent System

A Claude Code-based agentic job search workflow built on markdown skill files, SQLite, and a live analytics dashboard.

## What This Is

A personal job search automation system built with Claude Code. It uses a multi-agent architecture where a central orchestrator (SERA) routes tasks to specialized subagents for job review, email scanning, and application logging.

## Architecture
```
├── CLAUDE.md                    # Session rules — loaded automatically by Claude Code
├── config.md                    # Shared config — file paths, score tiers, filter rules
├── integrity.py                 # Sole gatekeeper for job-tracker.db writes
├── agents/
│   ├── sera.md                  # Orchestrator — personality, routing, session state
│   ├── job-match.md             # Job review — parse, score, DB write
│   ├── email-scanner-v2.md      # Gmail scan — digest classify, ingest, rejection close
│   └── job-log.md               # Application log — UI compliance CSV writer
└── dashboard-app/               # FastAPI + Vite live dashboard (not in this repo)
```

## How It Works

1. Launch Claude Code and say "scan my emails" or paste a job posting
2. SERA reads `CLAUDE.md` and agent files automatically before routing
3. Job reviews score against two resume tracks (PM and Automation), using a 4-component weighted matrix with a hard-requirement gate
4. Email scans extract roles from digests, ingest through `integrity.py` filter gates, and auto-close rejections matched to existing applications
5. All writes to `job-tracker.db` route through `integrity.py` — agents never touch the DB directly
6. Dashboard reads both SQLite DBs and renders live analytics

## Agent Design

Each agent is a markdown file loaded as a system prompt. SERA orchestrates — she never scores jobs herself. Subagents handle discrete tasks with defined inputs, outputs, and logging requirements.

`CLAUDE.md` enforces session-level rules that cannot be skipped: mandatory file reads before action, Gmail tool restrictions, logging compliance, and execution discipline.

## integrity.py — DB Gatekeeper

Sole entry point for all `job-tracker.db` writes. Actions:

- `ingest` — apply filter gates (Verizon hard-stop, comp floor, NJ commute, non-target roles, PMP-required); approved rows land Pending, filtered rows land Pass
- `insert` — direct insert without gates (reserved for callers that have already filtered)
- `write_review` — atomic verdict write (verdict fields + score from components). Gate FAIL auto-transitions to Pass with closure note; Gate PASS transitions Pending → Reviewed
- `update_status` — status transitions; enforces `applied_date` requirement for Applied+ states
- `update_score`, `mark_for_rescore` — score lifecycle
- `resolve_id`, `bulk_resolve` — exact + fuzzy match for company + role
- `age_pass` — auto-Pass stale Reviewed rows after 10 days (runs on every invocation)
- `audit` — read-only data integrity report
- `event_log_write`, `write_flag`, `resolve_flag` — monitor.db logging
- `backfill_closed_at` — one-shot maintenance

## Dashboard

The live dashboard is a FastAPI backend + Vite frontend (lives outside this repo, in `dashboard-app/`):

- Backend: FastAPI on `:8001` reading `job-tracker.db` and `monitor.db`
- Frontend: Vite dev server on `:5173` for local development
- Tabs: pipeline stats, score distribution, source breakdown, comp coverage, active applications, scan performance over time, quality flags

## Stack

- Claude Code (agent runtime)
- SQLite (`job-tracker.db`, `monitor.db`)
- Python 3 + FastAPI (dashboard backend)
- Vite + TypeScript (dashboard frontend)
- Gmail MCP (email access — `search_threads`, `get_thread`, `list_labels`, `create_draft`, `create_label`, `list_drafts`)

## Setup

1. Clone the repo
2. Copy agent files to your Claude Code skill directory
3. Update `<username>` paths in all files to match your machine
4. Fill in `[COMP_FLOOR]`, `[HOME_LOCATION]`, `[PLACEMENT_RESTRICTIONS]` in config files
5. Initialize `job-tracker.db` and `monitor.db` schemas (see `integrity.py` for the `reviewed_postings` table shape; monitor.db needs `event_log`, `quality_flags`, `scan_metrics`, `session_log`)
6. Wire up the live dashboard separately if desired (FastAPI + Vite)
```