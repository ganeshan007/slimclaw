---
name: setup
description: Run initial SlimClaw setup. Use when user wants to install dependencies, authenticate WhatsApp, register their main channel, or start the background service. Triggers on "setup", "install", "configure slimclaw", or first-time setup requests.
---

# SlimClaw Setup

Run setup steps automatically. Only pause when user action is required (WhatsApp authentication, configuration choices).

**Principle:** When something is broken or missing, fix it. Don't tell the user to go fix it themselves unless it genuinely requires their manual action (e.g. scanning a QR code, pasting a secret token). If a dependency is missing, install it. If a service won't start, diagnose and repair. Ask the user for permission when needed, then do the work.

**UX Note:** Use `AskUserQuestion` for all user-facing questions.

## 1. Welcome & Name Your Bot

Start with personalization — this is the user's assistant, let them own it.

AskUserQuestion: What would you like to name your bot? This name becomes the trigger word in group chats (e.g. @TARS). Default: **TARS**.

Store the chosen name for use in all subsequent steps. If the name differs from "TARS":

1. Update `src/slimclaw/config.py` — change the default in the `ASSISTANT_NAME` fallback:
```python
ASSISTANT_NAME: str = (
    os.environ.get("ASSISTANT_NAME") or _env_config.get("ASSISTANT_NAME") or "ChosenName"
)
```

2. Update `groups/global/CLAUDE.md` — replace the heading and persona line:
```markdown
# ChosenName

You are ChosenName, a personal assistant.
```

3. Update `groups/main/CLAUDE.md` — same heading and persona line, plus any trigger references (`"trigger": "@ChosenName"`).

4. Update `README.md` — replace `@TARS` with `@ChosenName` in the usage examples.

If the user picks "TARS" (or accepts the default), no file changes are needed — everything already uses TARS.

## 2. Check Environment

Verify prerequisites:

```bash
echo "=== SlimClaw Environment Check ==="

echo -n "PYTHON: "
python3 --version 2>/dev/null && echo "OK" || echo "MISSING"

echo -n "PYTHON_MIN_VERSION: "
python3 -c "import sys; print('OK' if sys.version_info >= (3,11) else 'TOO_OLD')" 2>/dev/null || echo "MISSING"

echo -n "PIP: "
pip3 --version &>/dev/null && echo "OK" || echo "MISSING"

echo -n "DOCKER: "
docker info &>/dev/null && echo "running" || (docker --version &>/dev/null && echo "installed_not_running" || echo "not_found")

echo -n "LIBMAGIC: "
python3 -c "import ctypes.util; print('OK' if ctypes.util.find_library('magic') else 'MISSING')" 2>/dev/null || echo "MISSING"

echo -n "HAS_ENV: "
[ -f .env ] && echo "true" || echo "false"

echo -n "HAS_AUTH: "
[ -f store/auth/neonize.db ] && echo "true" || echo "false"

echo -n "HAS_REGISTERED_GROUPS: "
python3 -c "
import sqlite3, os
db = 'store/messages.db'
if os.path.exists(db):
    c = sqlite3.connect(db)
    count = c.execute('SELECT COUNT(*) FROM registered_groups').fetchone()[0]
    print('true' if count > 0 else 'false')
else:
    print('false')
" 2>/dev/null || echo "false"

echo -n "PLATFORM: "
uname -s
```

- If HAS_AUTH=true, note that WhatsApp auth exists, offer to skip step 6
- If HAS_REGISTERED_GROUPS=true, note existing config, offer to skip or reconfigure

**If PYTHON missing or TOO_OLD:**

Python 3.11+ is required. Ask the user if they'd like you to install it:
- macOS: `brew install python@3.12` (if brew available)
- Linux: `sudo apt-get install python3.12 python3.12-venv` or use pyenv

**If LIBMAGIC missing:**

Install automatically:
- macOS: `brew install libmagic`
- Linux: `sudo apt-get install libmagic1`

## 3. Install Dependencies

```bash
cd PROJECT_ROOT
pip install -e ".[dev]"
```

**If failed:** Common fixes:
1. Missing build tools: `xcode-select --install` (macOS) or `sudo apt-get install build-essential` (Linux)
2. Permission errors: suggest using a virtual environment (`python3 -m venv .venv && source .venv/bin/activate`)
3. neonize build failure: ensure Go is installed (`brew install go` on macOS)

Verify the install worked:
```bash
slimclaw --help 2>/dev/null || python3 -m slimclaw --help
```

## 4. Container Runtime

### 4a. Install/Start Docker

- DOCKER=running -> continue to 4b
- DOCKER=installed_not_running -> start Docker: `open -a Docker` (macOS) or `sudo systemctl start docker` (Linux). Wait 15s, re-check with `docker info`.
- DOCKER=not_found -> **ask the user for confirmation before installing.** Tell them Docker is required for running agents.
  - macOS: `brew install --cask docker`, then `open -a Docker`
  - Linux: `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER`

### 4b. Build Container Image

```bash
./container/build.sh
```

If `container/` doesn't exist and NanoClaw is installed nearby:
```bash
ln -s ../nanoclaw/container container
./container/build.sh
```

**If BUILD failed:** Read the error output.
- Cache issue: `docker builder prune -f`, then retry
- Missing Dockerfile: check the container symlink points to the right location

**Test the image:**
```bash
echo '{}' | docker run -i --entrypoint /bin/echo nanoclaw-agent:latest "OK"
```

## 5. Claude Authentication

If HAS_ENV=true from step 2, read `.env` and check if it already has `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`. If so, confirm with user: "You already have Claude credentials configured. Want to keep them or reconfigure?" If keeping, skip to step 6.

AskUserQuestion: Claude subscription (Pro/Max) vs Anthropic API key?

**Subscription:** Tell the user:
1. Open another terminal and run: `claude setup-token`
2. Copy the token it outputs
3. Add it to the `.env` file in the project root: `CLAUDE_CODE_OAUTH_TOKEN=<token>`
4. Let me know when done

Do NOT ask the user to paste the token into the chat. Just tell them what to do, then wait for confirmation.

**API key:** Tell the user to add `ANTHROPIC_API_KEY=<key>` to the `.env` file in the project root, then let you know when done. Once confirmed, verify the `.env` file has the key.

## 6. WhatsApp Authentication

If HAS_AUTH=true from step 2, confirm with user: "WhatsApp credentials already exist. Want to keep them or re-authenticate?" If keeping, skip to step 7.

Run the auth script:
```bash
slimclaw-auth
```

This opens a **browser window** with a scannable QR code (also prints to terminal as fallback). The page auto-refreshes when the QR rotates. Tell the user:
1. Open WhatsApp on their phone
2. Go to Settings > Linked Devices > Link a Device
3. Scan the QR code in the browser window

Wait for confirmation that auth succeeded (credentials saved to `store/auth/neonize.db`). The browser page will show "Authenticated!" on success.

**If failed:**
- QR expired: re-run `slimclaw-auth` to generate a fresh QR
- Auth database locked: delete `store/auth/neonize.db` and retry
- neonize import error: ensure `libmagic` is installed and `pip install -e .` succeeded

## 7. Channel Type

AskUserQuestion: How do you want to talk to {BotName}?

**If bot shares user's personal phone number (same phone):**
1. Self-chat (chat with yourself) — Recommended. You message yourself and the bot responds.
2. Solo group (just you) — A group where you're the only member. Good if you want message history separate from self-chat.

**If bot has its own dedicated phone number:**
1. DM with the bot — Recommended. You message the bot's number directly.
2. Solo group with the bot — A group with just you and the bot.

To determine the phone situation, check if ASSISTANT_HAS_OWN_NUMBER is set in `.env`, or ask the user directly.

Do NOT show options that don't apply to the user's setup.

## 8. Discover & Select Group

**For personal/self-chat:** The JID is the user's phone number as `NUMBER@s.whatsapp.net`.

**For DM with bot's dedicated number:** Ask for the bot's phone number, construct JID as `NUMBER@s.whatsapp.net`.

**For group (solo or with bot):**
1. Start slimclaw briefly to sync groups:
```bash
timeout 30 slimclaw 2>/dev/null || true
```
2. List available groups:
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('store/messages.db')
rows = c.execute('SELECT jid, name FROM chats WHERE is_group = 1 ORDER BY last_message_time DESC LIMIT 20').fetchall()
for jid, name in rows:
    print(f'{jid} | {name}')
"
```
3. Present group names as AskUserQuestion options (show names only, not JIDs). Include an "Other" option if their group isn't listed.

## 9. Register Main Channel

Register the selected group as the main channel, using the bot name from step 1:

```bash
python3 -c "
import sqlite3, json
from datetime import datetime, timezone
c = sqlite3.connect('store/messages.db')
c.execute('''INSERT OR REPLACE INTO registered_groups
    (jid, name, folder, trigger_pattern, container_config, requires_trigger, added_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)''',
    ('JID', 'main', 'main', '@BOTNAME', json.dumps({}), 0, datetime.now(timezone.utc).isoformat()))
c.commit()
"
```

Replace `JID` with the value from step 8 and `BOTNAME` with the name from step 1.

## 10. Mount Allowlist

AskUserQuestion: Want the agent to access directories outside the SlimClaw project? (Git repos, project folders, documents, etc.)

**If no:** Create an empty allowlist:
```bash
mkdir -p ~/.config/slimclaw
echo '{"allowed_roots":[],"blocked_patterns":[],"non_main_read_only":true}' > ~/.config/slimclaw/mount-allowlist.json
```

**If yes:** Collect directory paths and permissions. Build the JSON:
```bash
mkdir -p ~/.config/slimclaw
cat > ~/.config/slimclaw/mount-allowlist.json << 'EOF'
{
  "allowed_roots": [
    {"path": "/path/to/dir", "readonly": false}
  ],
  "blocked_patterns": [],
  "non_main_read_only": true
}
EOF
```

## 11. Start Service

### macOS (launchd)

Create the plist:
```bash
cat > ~/Library/LaunchAgents/com.slimclaw.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.slimclaw</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>-m</string>
        <string>slimclaw</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$(pwd)</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$(pwd)/logs/slimclaw.log</string>
    <key>StandardErrorPath</key>
    <string>$(pwd)/logs/slimclaw.error.log</string>
</dict>
</plist>
EOF

mkdir -p logs
launchctl load ~/Library/LaunchAgents/com.slimclaw.plist
```

### Linux (systemd)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/slimclaw.service << EOF
[Unit]
Description=SlimClaw Assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(which python3) -m slimclaw
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now slimclaw
```

## 12. Verify

```bash
echo "=== SlimClaw Verification ==="

echo -n "SERVICE: "
launchctl list 2>/dev/null | grep -q slimclaw && echo "running" || (systemctl --user is-active slimclaw 2>/dev/null || echo "not_found")

echo -n "CREDENTIALS: "
(grep -q "CLAUDE_CODE_OAUTH_TOKEN=sk-" .env 2>/dev/null || grep -q "ANTHROPIC_API_KEY=sk-" .env 2>/dev/null) && echo "OK" || echo "MISSING"

echo -n "WHATSAPP_AUTH: "
[ -f store/auth/neonize.db ] && echo "OK" || echo "MISSING"

echo -n "REGISTERED_GROUPS: "
python3 -c "
import sqlite3
c = sqlite3.connect('store/messages.db')
count = c.execute('SELECT COUNT(*) FROM registered_groups').fetchone()[0]
print(count)
" 2>/dev/null || echo "0"

echo -n "CONTAINER_IMAGE: "
echo '{}' | docker run -i --entrypoint /bin/echo nanoclaw-agent:latest "OK" 2>/dev/null || echo "MISSING"

echo -n "MOUNT_ALLOWLIST: "
[ -f ~/.config/slimclaw/mount-allowlist.json ] && echo "OK" || echo "MISSING"
```

Tell the user: "Your bot **{BotName}** is ready! Send a message in your main channel to try it out."

- In the main channel: just type normally, no trigger needed
- In group chats: start messages with `@{BotName}`

Show the log tail command: `tail -f logs/slimclaw.log`

## Troubleshooting

**Service not starting:** Check `logs/slimclaw.error.log`. Common causes: wrong Python path in plist, missing `.env`, missing WhatsApp auth, neonize import failure.

**Container agent fails:** Ensure Docker is running. Check container logs in `groups/main/logs/container-*.log`.

**No response to messages:** Verify the trigger pattern matches. Main channel doesn't need a prefix. Check the registered JID: `sqlite3 store/messages.db "SELECT * FROM registered_groups"`. Check `logs/slimclaw.log`.

**WhatsApp disconnected:** Run `slimclaw-auth` to re-authenticate, then restart the service.

**neonize import error:** Ensure `libmagic` is installed (`brew install libmagic` on macOS, `sudo apt-get install libmagic1` on Linux).
