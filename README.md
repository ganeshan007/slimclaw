# SlimClaw

Personal Claude assistant accessible via WhatsApp. Messages route to Claude Agent SDK running in Docker containers. Each group gets isolated filesystem and memory.

Inspired by [NanoClaw](https://github.com/qwibitai/nanoclaw) and [nanobot](https://github.com/HKUDS/nanobot), built from scratch in Python with a focus on low memory overhead, easy onboarding, and extensibility through skills.

## Features

- **Multi-app** — WhatsApp built-in, add Telegram/Discord/Slack/Signal by dropping in one file
- **Standalone CLI setup** — `slimclaw-setup` walks you through everything, no AI IDE needed
- **Model selection** — choose Haiku 4.5 (fast), Sonnet 4.6 (balanced), or Opus 4.6 (most capable)
- **Isolated groups** — each group has its own memory, filesystem, and container
- **Main channel** — private admin chat, no trigger needed. Say "join Family Chat" to add groups
- **Group management** — register/unregister groups via IPC, auto-detection of @mentions in unregistered groups
- **Scheduled tasks** — cron, interval, or one-shot jobs
- **Web access** — search and browse from inside containers
- **Agent Swarms** — teams of agents collaborating on tasks
- **Skills over Features** — add capabilities via Claude Code skills, not code bloat

### Benchmarks

| Metric | SlimClaw (Python) | NanoClaw (Node.js) |
|---|---|---|
| Idle RSS (all modules loaded) | **30.2 MB** | 100.3 MB |
| Final RSS (after workload) | **54.1 MB** | 138.9 MB |
| SQLite insert (10K msgs) | 482 ms | **52 ms** |
| SQLite query (10K rows) | 35 ms | **7.6 ms** |
| Dependencies | 6 | 9 |
| Source lines | 4,860 | 6,650 |

Python uses **2x less memory**. Node.js is faster at SQLite (native C++ addon).

## Quick Start

```bash
pip install slimclaw
slimclaw-setup
```

Or from source:

```bash
git clone https://github.com/ganeshan007/slimclaw.git
cd slimclaw
pip install -e ".[dev]"
slimclaw-setup
```

### Setup Options

**Interactive wizard** (no Claude Code needed):
```bash
slimclaw-setup
```

**AI-guided setup** (with Claude Code):
```bash
claude
# then type /setup
```

**Manual:**
```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
./container/build.sh
slimclaw-auth
slimclaw
```

## Skills

Capabilities are added via [Claude Code skills](https://docs.anthropic.com/en/docs/claude-code/skills) in `.claude/skills/` instead of bloating the codebase.

| Skill | Command | Purpose |
|-------|---------|---------|
| Setup | `/setup` | First-time installation, authentication, service configuration |
| Customize | `/customize` | Add apps, integrations, change behavior |
| Add App | `/add-app-template` | Guide for adding any new messaging app |
| Debug | `/debug` | Container issues, logs, troubleshooting |

Open Claude Code in the project directory and type the command to invoke a skill.

## Architecture

```
Apps (WhatsApp, Telegram, ...) --> SQLite --> asyncio poll loop --> Docker container (Claude Agent SDK) --> Response
```

Single Python process. `asyncio` event loop with three concurrent tasks:

- **Message loop** (2s poll) — detects new messages, checks triggers, spawns containers
- **IPC watcher** (1s poll) — processes file-based IPC from containers (outbound messages, group registration/unregistration, task management)
- **Task scheduler** (60s poll) — runs due scheduled tasks, closes containers after 5s idle

### App System

Apps are auto-discovered from `src/slimclaw/channels/` at startup. Adding a new app (Telegram, Discord, Slack, Signal) requires creating **one file** — no changes to `main.py` or core code:

1. Create `src/slimclaw/channels/{app_name}.py` implementing the `Channel` protocol (6 methods)
2. The registry discovers it automatically via `pkgutil.iter_modules`
3. Optional deps are guarded with `try/except ImportError` — missing SDKs are silently skipped

Control which apps load via `.env`:
```
ENABLED_APPS=whatsapp,telegram   # omit to load all discovered apps
```

See `.claude/skills/add-app-template/SKILL.md` for a complete guide and code template.

### Key Files

```
src/slimclaw/
  main.py               # Orchestrator: startup, message loop, agent invocation
  setup_cli.py          # Interactive setup wizard (no Claude Code needed)
  channels/
    registry.py         # Auto-discovery of app channels
    whatsapp.py         # WhatsApp via neonize (Go-based, wraps whatsmeow)
  db.py                 # SQLite: messages, groups, sessions, tasks, state
  container_runner.py   # Docker subprocess, output marker parsing, mount building
  group_queue.py        # Per-group concurrency control (max 5 containers)
  ipc.py                # File-based IPC watcher with authorization
  task_scheduler.py     # Cron/interval/once scheduled tasks
  mount_security.py     # Mount allowlist validation
  router.py             # XML message formatting, internal tag stripping
  types.py              # Dataclasses, Channel protocol, AppOpts
  config.py             # Constants, paths, ENABLED_APPS
  logger.py             # Lazy-loaded structlog
```

### Optimizations

- **`__slots__` dataclasses** — 19% less per-object memory than regular dataclasses
- **Lazy structlog import** — deferred until first log call, saves 20 MB startup RSS
- **SQLite WAL mode + autocommit** — no per-statement `commit()` overhead

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

The container runs:

- Node.js 22 + Claude Agent SDK
- Chromium (for browser automation)
- Per-group `.claude/` sessions
- MCP server for task scheduling and messaging

Build it once:

```bash
./container/build.sh
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
