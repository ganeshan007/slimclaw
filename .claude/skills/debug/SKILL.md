---
name: debug
description: Debug container agent issues. Use when things aren't working, container fails, authentication problems, or to understand how the container system works. Covers logs, environment variables, mounts, and common issues.
---

# SlimClaw Container Debugging

This guide covers debugging the containerized agent execution system.

## Architecture Overview

```
Host (macOS/Linux)                    Container (Linux VM)
─────────────────────────────────────────────────────────────
src/slimclaw/container_runner.py      container/agent-runner/
    │                                      │
    │ spawns container                     │ runs Claude Agent SDK
    │ with volume mounts                   │ with MCP servers
    │                                      │
    ├── .env (secrets) ──────────> /workspace/env-dir/env
    ├── groups/{folder} ─────────> /workspace/group
    ├── data/ipc/{folder} ───────> /workspace/ipc
    ├── data/sessions/{folder}/.claude/ ──> /home/node/.claude/ (isolated per-group)
    └── (main only) project root ──> /workspace/project
```

**Important:** The container runs as user `node` with `HOME=/home/node`. Session files must be mounted to `/home/node/.claude/` (not `/root/.claude/`) for session resumption to work.

## Log Locations

| Log | Location | Content |
|-----|----------|---------|
| **Main app output** | stdout/stderr (or `logs/slimclaw.log` if using launchd) | Host-side WhatsApp, routing, container spawning |
| **Main app errors** | stderr (or `logs/slimclaw.error.log` if using launchd) | Host-side errors |
| **Container run logs** | `groups/{folder}/logs/container-*.log` | Per-run: input, mounts, stderr, stdout |

## Enabling Debug Logging

```bash
# For development
LOG_LEVEL=debug slimclaw

# For launchd service, add to plist EnvironmentVariables:
<key>LOG_LEVEL</key>
<string>debug</string>
```

Debug level shows:
- Full mount configurations
- Container command arguments
- Real-time container stderr
- Message routing decisions

## Common Issues

### 1. "Claude Code process exited with code 1"

**Check the container log file** in `groups/{folder}/logs/container-*.log`

Common causes:

#### Missing Authentication
```
Invalid API key · Please run /login
```
**Fix:** Ensure `.env` file exists with either OAuth token or API key:
```bash
cat .env  # Should show one of:
# CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...  (subscription)
# ANTHROPIC_API_KEY=sk-ant-api03-...        (pay-per-use)
```

#### Root User Restriction
```
--dangerously-skip-permissions cannot be used with root/sudo privileges
```
**Fix:** Container must run as non-root user. Check Dockerfile has `USER node`.

### 2. neonize / libmagic Import Error

```
ImportError: failed to find libmagic
OSError: cannot load library 'libmagic'
```

**Fix:**
```bash
# macOS
brew install libmagic

# Linux
sudo apt-get install libmagic1

# Then reinstall
pip install -e .
```

### 3. WhatsApp Disconnected

Symptoms: no new messages arriving, logs show disconnect events.

**Fix:**
```bash
# Re-authenticate
slimclaw-auth

# Restart the service
launchctl unload ~/Library/LaunchAgents/com.slimclaw.plist
launchctl load ~/Library/LaunchAgents/com.slimclaw.plist
# Or on Linux:
systemctl --user restart slimclaw
```

If persistent: delete auth and re-pair:
```bash
rm store/auth/neonize.db
slimclaw-auth
```

### 4. Container Auth Not Reaching Agent

Environment variables passed via `-e` may be lost when using `-i` (interactive/piped stdin).

**Workaround:** SlimClaw extracts only authentication variables (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`) from `.env` and mounts them for sourcing inside the container. Other env vars are not exposed.

To verify env vars reach the container:
```bash
echo '{}' | docker run -i \
  -v $(pwd)/data/env:/workspace/env-dir:ro \
  --entrypoint /bin/bash nanoclaw-agent:latest \
  -c 'export $(cat /workspace/env-dir/env | xargs); echo "OAuth: ${#CLAUDE_CODE_OAUTH_TOKEN} chars, API: ${#ANTHROPIC_API_KEY} chars"'
```

### 5. IPC Not Processing

Symptoms: agent runs but no messages sent, tasks not created.

**Check:** IPC directory structure exists:
```bash
ls -la data/ipc/
ls -la data/ipc/main/messages/ data/ipc/main/tasks/
```

**Check:** IPC watcher is running (look for poll loop in logs):
```bash
LOG_LEVEL=debug slimclaw 2>&1 | grep -i ipc
```

**Check:** Malformed IPC files (moved to errors/):
```bash
ls data/ipc/*/errors/ 2>/dev/null
```

### 6. Mount Issues

To check what's mounted inside a container:
```bash
docker run --rm --entrypoint /bin/bash nanoclaw-agent:latest -c 'ls -la /workspace/'
```

Expected structure:
```
/workspace/
├── env-dir/env           # Environment file
├── group/                # Current group folder (cwd)
├── project/              # Project root (main channel only)
├── global/               # Global CLAUDE.md (non-main only)
├── ipc/                  # Inter-process communication
│   ├── messages/         # Outgoing messages
│   ├── tasks/            # Task commands
│   └── input/            # Stdin for active containers
└── extra/                # Additional custom mounts
```

### 7. Session Not Resuming

If sessions aren't being resumed (new session ID every time):

**Root cause:** The SDK looks for sessions at `$HOME/.claude/projects/`. Inside the container, `HOME=/home/node`, so it looks at `/home/node/.claude/projects/`.

**Check the mount path:**
```bash
grep -n "home/node/.claude" src/slimclaw/container_runner.py
```

**Verify sessions are accessible:**
```bash
ls -la data/sessions/main/.claude/ 2>/dev/null
```

To clear sessions:
```bash
# Clear all sessions for all groups
rm -rf data/sessions/

# Clear sessions for a specific group
rm -rf data/sessions/{groupFolder}/.claude/

# Also clear the session ID from SlimClaw's tracking
sqlite3 store/messages.db "DELETE FROM sessions WHERE group_folder = '{groupFolder}'"
```

### 8. Permission Issues

The container runs as user `node` (uid 1000). Check ownership:
```bash
docker run --rm --entrypoint /bin/bash nanoclaw-agent:latest -c '
  whoami
  ls -la /workspace/
  ls -la /app/
'
```

## Manual Container Testing

### Test the full agent flow:
```bash
mkdir -p data/env groups/test
cp .env data/env/env

echo '{"prompt":"What is 2+2?","groupFolder":"test","chatJid":"test@g.us","isMain":false}' | \
  docker run -i \
  -v $(pwd)/data/env:/workspace/env-dir:ro \
  -v $(pwd)/groups/test:/workspace/group \
  -v $(pwd)/data/ipc:/workspace/ipc \
  nanoclaw-agent:latest
```

### Test Claude Code directly:
```bash
docker run --rm --entrypoint /bin/bash \
  -v $(pwd)/data/env:/workspace/env-dir:ro \
  nanoclaw-agent:latest -c '
  export $(cat /workspace/env-dir/env | xargs)
  claude -p "Say hello" --dangerously-skip-permissions --allowedTools ""
'
```

### Interactive shell in container:
```bash
docker run --rm -it --entrypoint /bin/bash nanoclaw-agent:latest
```

## IPC Debugging

The container communicates back to the host via files in `/workspace/ipc/`:

```bash
# Check pending messages
ls -la data/ipc/main/messages/

# Check pending task operations
ls -la data/ipc/main/tasks/

# Read a specific IPC file
cat data/ipc/main/messages/*.json

# Check current tasks snapshot
cat data/ipc/main/current_tasks.json
```

**IPC file types:**
- `messages/*.json` — Agent writes: outgoing messages
- `tasks/*.json` — Agent writes: task operations (schedule, pause, resume, cancel)
- `current_tasks.json` — Host writes: read-only snapshot of scheduled tasks
- `available_groups.json` — Host writes: read-only list of WhatsApp groups (main only)
- `input/*.json` — Host writes: messages forwarded to active container stdin

## Quick Diagnostic Script

Run this to check common issues:

```bash
echo "=== SlimClaw Container Diagnostics ==="

echo -e "\n1. Authentication configured?"
[ -f .env ] && (grep -q "CLAUDE_CODE_OAUTH_TOKEN=sk-" .env || grep -q "ANTHROPIC_API_KEY=sk-" .env) && echo "OK" || echo "MISSING - add CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY to .env"

echo -e "\n2. Container runtime running?"
docker info &>/dev/null && echo "OK" || echo "NOT RUNNING - start Docker Desktop (macOS) or sudo systemctl start docker (Linux)"

echo -e "\n3. Container image exists?"
echo '{}' | docker run -i --entrypoint /bin/echo nanoclaw-agent:latest "OK" 2>/dev/null || echo "MISSING - run ./container/build.sh"

echo -e "\n4. WhatsApp authenticated?"
[ -f store/auth/neonize.db ] && echo "OK" || echo "MISSING - run slimclaw-auth"

echo -e "\n5. libmagic available?"
python3 -c "import ctypes.util; exit(0 if ctypes.util.find_library('magic') else 1)" 2>/dev/null && echo "OK" || echo "MISSING - brew install libmagic (macOS) or sudo apt-get install libmagic1 (Linux)"

echo -e "\n6. Groups directory?"
ls groups/ 2>/dev/null || echo "MISSING - run setup"

echo -e "\n7. Registered groups?"
python3 -c "
import sqlite3, os
db = 'store/messages.db'
if os.path.exists(db):
    c = sqlite3.connect(db)
    rows = c.execute('SELECT jid, name, folder FROM registered_groups').fetchall()
    for r in rows: print(f'  {r[2]}: {r[1]} ({r[0]})')
    if not rows: print('  None registered')
else:
    print('  No database yet')
" 2>/dev/null || echo "  Error reading database"

echo -e "\n8. Recent container logs?"
ls -t groups/*/logs/container-*.log 2>/dev/null | head -3 || echo "No container logs yet"

echo -e "\n9. Mount allowlist?"
[ -f ~/.config/slimclaw/mount-allowlist.json ] && echo "OK" || echo "MISSING - create with /setup"
```

## Rebuilding After Changes

```bash
# Reinstall Python package
pip install -e .

# Rebuild container (use --no-cache for clean rebuild)
./container/build.sh

# Or force full rebuild
docker builder prune -af
./container/build.sh
```
