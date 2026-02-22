# SlimClaw

Python rewrite of [NanoClaw](https://github.com/qwibitai/nanoclaw). Same functionality, half the memory.

Personal Claude assistant accessible via WhatsApp. Messages route to Claude Agent SDK running in Docker containers. Each group gets isolated filesystem and memory.

## Why

NanoClaw is Node.js. SlimClaw is the same thing in Python — leaner runtime, fewer dependencies, and optimized for low memory overhead. Built to profile, compare, and run where Python is preferred.

### Benchmarks (vs NanoClaw Node.js)

| Metric | SlimClaw (Python) | NanoClaw (Node.js) |
|---|---|---|
| Idle RSS (all modules loaded) | **30.5 MB** | 100.3 MB |
| Final RSS (after workload) | **53.5 MB** | 138.9 MB |
| SQLite insert (10K msgs) | 475 ms | **52 ms** |
| SQLite query (10K rows) | 37 ms | **7.6 ms** |
| Dependencies | 6 | 9 |
| Source lines | 3,651 | 3,700 |
| Tests | 81 | 81 |

Python uses **2x less memory**. Node.js is faster at SQLite (native C++ addon). Both run the same Docker containers.

## Quick Start

```bash
git clone https://github.com/ganeshan007/slimclaw.git
cd slimclaw
pip install -e ".[dev]"
```

### Setup

Interactive wizard (no Claude Code needed):

```bash
slimclaw-setup
```

Or with Claude Code for AI-guided setup:

```bash
claude
# then type /setup
```

Or manually:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
./container/build.sh
slimclaw-auth
slimclaw
```

## Skills

SlimClaw follows the "Skills over Features" philosophy from NanoClaw. Instead of bloating the codebase, capabilities are added via [Claude Code skills](https://docs.anthropic.com/en/docs/claude-code/skills) in `.claude/skills/`.

| Skill | Command | Purpose |
|-------|---------|---------|
| Setup | `/setup` | First-time installation, authentication, service configuration |
| Customize | `/customize` | Add channels, integrations, change behavior |
| Debug | `/debug` | Container issues, logs, troubleshooting |

Open Claude Code in the project directory and type the command to invoke a skill.

## Architecture

```
WhatsApp (neonize) --> SQLite --> asyncio poll loop --> Docker container (Claude Agent SDK) --> Response
```

Single Python process. `asyncio` event loop with three concurrent tasks:

- **Message loop** (2s poll) — detects new messages, checks triggers, spawns containers
- **IPC watcher** (1s poll) — processes file-based IPC from containers (outbound messages, task management)
- **Task scheduler** (60s poll) — runs due scheduled tasks

### Key Files

```
src/slimclaw/
  main.py              # Orchestrator: startup, message loop, agent invocation
  channels/whatsapp.py # WhatsApp via neonize (Go-based, wraps whatsmeow)
  db.py                # SQLite: messages, groups, sessions, tasks, state
  container_runner.py  # Docker subprocess, output marker parsing, mount building
  group_queue.py       # Per-group concurrency control (max 5 containers)
  ipc.py               # File-based IPC watcher with authorization
  task_scheduler.py    # Cron/interval/once scheduled tasks
  mount_security.py    # Mount allowlist validation
  router.py            # XML message formatting, internal tag stripping
  types.py             # Dataclasses with __slots__
  config.py            # Constants and paths
  logger.py            # Lazy-loaded structlog
```

### Optimizations

- **`__slots__` dataclasses** — 19% less per-object memory than regular dataclasses
- **Lazy structlog import** — deferred until first log call, saves 20 MB startup RSS
- **SQLite WAL mode + autocommit** — no per-statement `commit()` overhead
- **Shared container image** — uses the same `nanoclaw-agent:latest` Docker image

## What It Does

- **WhatsApp I/O** — message Claude from your phone
- **Isolated groups** — each group has its own `CLAUDE.md`, filesystem, container
- **Main channel** — private admin control, no trigger needed
- **Scheduled tasks** — cron, interval, or one-shot jobs
- **Web access** — search and fetch from inside containers
- **Agent Swarms** — teams of agents collaborating on tasks
- **IPC authorization** — main group can do anything; other groups are sandboxed

## Usage

Talk to your assistant (default trigger: `@TARS`):

```
@TARS summarize the last week of messages
@TARS schedule a daily standup reminder at 9am
@TARS what's the weather in San Francisco
```

From the main channel (no trigger needed):

```
list all scheduled tasks
join the Family Chat group
pause the Monday briefing task
```

## Container

SlimClaw shares the container image with NanoClaw. The container runs:

- Node.js 22 + Claude Agent SDK
- Chromium (for browser automation)
- Per-group `.claude/` sessions
- MCP server for task scheduling and messaging

Build it once:

```bash
./container/build.sh
```

Symlink it if you already have NanoClaw:

```bash
ln -s ../nanoclaw/container container
```

## Development

```bash
# Run tests
pytest

# Run with debug logging
LOG_LEVEL=debug slimclaw

# Profile memory
python benchmarks/profile_python.py
```

## Requirements

- Python 3.11+
- Docker
- macOS or Linux
- `libmagic` (`brew install libmagic` on macOS)

## License

MIT
