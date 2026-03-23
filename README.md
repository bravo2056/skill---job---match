# Job Search Agent System

A Claude Code-based agentic job search workflow built on markdown skill files, SQLite, and a live analytics dashboard.

## What This Is

A personal job search automation system built with Claude Code. It uses a multi-agent architecture where a central orchestrator (SERA) routes tasks to specialized subagents for job review, email scanning, and application logging.

## Architecture
```
├── CLAUDE.md                    # Session rules — loaded automatically by Claude Code
├── config.md                    # Shared config — file paths, score tiers, filter rules
├── agents/
│   ├── sera.md                  # Orchestrator — personality, routing, session state
│   ├── job-match.md             # Job review — parse, score, DB write
│   ├── email-scanner.md         # Gmail scan — tier classify, filter, cleanup list
│   └── job-log.md               # Application log — UI compliance CSV writer
├── dashboard.html               # Analytics dashboard — Chart.js, dark theme
├── server.py                    # Flask server — serves dashboard, /refresh endpoint
└── generate-monitor-data.py     # Data generator — reads SQLite, writes monitor-data.js
```

## How It Works

1. Launch Claude Code and say "scan my emails" or paste a job posting
2. SERA reads `CLAUDE.md` and agent files automatically before routing
3. Job reviews score against two resume tracks (PM and Automation)
4. Email scans tier-classify roles from Gmail digests into Apply / Borderline / Filtered
5. All results write to SQLite (`job-tracker.db`, `monitor.db`)
6. Dashboard reads both DBs and renders live analytics

## Agent Design

Each agent is a markdown file loaded as a system prompt. SERA orchestrates — she never scores jobs herself. Subagents handle discrete tasks with defined inputs, outputs, and logging requirements.

`CLAUDE.md` enforces session-level rules that cannot be skipped: mandatory file reads before action, Gmail tool restrictions, logging compliance, and execution discipline.

## Dashboard

Two tabs:
- **Job Match** — pipeline stats, score distribution, source breakdown, comp coverage, active applications table
- **Agents** — session health, event distribution, quality flags, scan performance over time

Run `server.py` to serve the dashboard at `localhost:5500`. Data refreshes on page load and on the Refresh button.

## Stack

- Claude Code (agent runtime)
- SQLite (job-tracker.db, monitor.db)
- Flask (dashboard server)
- Chart.js 4.4.2 (dashboard charts)
- Gmail MCP (email access)

## Setup

1. Clone the repo
2. Copy agent files to `~/.claude/commands/` or your Claude Code skill directory
3. Update `<username>` paths in all files to match your machine
4. Fill in `[COMP_FLOOR]`, `[HOME_LOCATION]`, `[PLACEMENT_RESTRICTIONS]` in config files
5. Run `pip install flask`
6. Run `python server.py` to launch the dashboard
```