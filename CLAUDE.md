# SlimClaw

Personal Claude assistant via WhatsApp. Inspired by NanoClaw and nanobot. See [README.md](README.md) for setup.

## Quick Context

Single Python process (asyncio) that connects to WhatsApp via neonize, routes messages to Claude Agent SDK running in Docker containers. Each group has isolated filesystem and memory.

## Key Files

| File | Purpose |
|------|---------|
| `src/slimclaw/main.py` | Orchestrator: startup, message loop, agent invocation |
| `src/slimclaw/channels/whatsapp.py` | WhatsApp connection via neonize (Go-based) |
| `src/slimclaw/ipc.py` | IPC watcher and task processing |
| `src/slimclaw/router.py` | Message formatting and outbound routing |
| `src/slimclaw/config.py` | Trigger pattern, paths, intervals |
| `src/slimclaw/container_runner.py` | Spawns agent containers with mounts |
| `src/slimclaw/task_scheduler.py` | Runs scheduled tasks |
| `src/slimclaw/db.py` | SQLite operations (WAL mode, autocommit) |
| `src/slimclaw/group_queue.py` | Per-group concurrency control (max 5 containers) |
| `src/slimclaw/mount_security.py` | Mount allowlist validation |
| `src/slimclaw/types.py` | Dataclasses with `__slots__`, Channel protocol |
| `src/slimclaw/logger.py` | Lazy-loaded structlog (saves 20 MB startup) |
| `groups/{name}/CLAUDE.md` | Per-group memory (isolated) |
| `container/skills/agent-browser.md` | Browser automation tool (available to all agents via Bash) |

## Skills

| Skill | When to Use |
|-------|-------------|
| `/setup` | First-time installation, authentication, service configuration |
| `/customize` | Adding channels, integrations, changing behavior |
| `/debug` | Container issues, logs, troubleshooting |

## Development

Run commands directly — don't tell the user to run them.

```bash
pip install -e ".[dev]"   # Install with dev dependencies
pytest                     # Run tests
slimclaw                   # Run the assistant
LOG_LEVEL=debug slimclaw   # Run with debug logging
./container/build.sh       # Rebuild agent container
```

Service management:
```bash
launchctl load ~/Library/LaunchAgents/com.slimclaw.plist
launchctl unload ~/Library/LaunchAgents/com.slimclaw.plist
```

## Container Build Cache

The container buildkit caches the build context aggressively. `--no-cache` alone does NOT invalidate COPY steps — the builder's volume retains stale files. To force a truly clean rebuild, prune the builder then re-run `./container/build.sh`.
