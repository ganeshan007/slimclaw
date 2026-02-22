---
name: customize
description: Add new capabilities or modify SlimClaw behavior. Use when user wants to add channels, change triggers, add integrations, modify the router, or make any other customizations. This is an interactive skill that asks questions to understand what the user wants.
---

# SlimClaw Customization

This skill helps users add capabilities or modify behavior. Use AskUserQuestion to understand what they want before making changes.

## Workflow

1. **Understand the request** - Ask clarifying questions
2. **Plan the changes** - Identify files to modify
3. **Implement** - Make changes directly to the code
4. **Test guidance** - Tell user how to verify

## Key Files

| File | Purpose |
|------|---------|
| `src/slimclaw/main.py` | Orchestrator: startup, message loop, agent invocation |
| `src/slimclaw/channels/whatsapp.py` | WhatsApp connection via neonize |
| `src/slimclaw/channels/__init__.py` | Channel exports |
| `src/slimclaw/ipc.py` | IPC watcher and task processing |
| `src/slimclaw/router.py` | Message formatting and outbound routing |
| `src/slimclaw/types.py` | Dataclasses, Channel protocol |
| `src/slimclaw/config.py` | Assistant name, trigger pattern, directories |
| `src/slimclaw/db.py` | Database initialization and queries |
| `src/slimclaw/container_runner.py` | Container spawning and mount building |
| `src/slimclaw/whatsapp_auth.py` | Standalone WhatsApp authentication |
| `groups/global/CLAUDE.md` | Global memory/persona (shared across non-main groups) |

## Common Customization Patterns

### Adding a New Input Channel (e.g., Telegram, Slack, Discord)

Questions to ask:
- Which channel? (Telegram, Slack, Discord, email, SMS, etc.)
- Same trigger word or different?
- Same memory hierarchy or separate?
- Should messages from this channel go to existing groups or new ones?

Implementation pattern:
1. Create `src/slimclaw/channels/{name}.py` implementing the `Channel` protocol from `src/slimclaw/types.py`:
```python
from slimclaw.types import Channel

class NewChannel:
    """Implements the Channel protocol."""
    name: str = "channel-name"

    async def connect(self) -> None: ...
    async def send_message(self, jid: str, text: str) -> None: ...
    def is_connected(self) -> bool: ...
    def owns_jid(self, jid: str) -> bool: ...
    async def disconnect(self) -> None: ...
    async def set_typing(self, jid: str, is_typing: bool) -> None: ...
```
2. See `src/slimclaw/channels/whatsapp.py` for the reference implementation
3. Add the channel instance in `main.py`'s `run()` function, wire `on_message` and `on_chat_metadata` callbacks
4. Messages are stored via the `on_message` callback; routing is automatic via `owns_jid()`
5. JID convention: prefix with channel identifier (e.g., `tg:` for Telegram, `dc:` for Discord)

### Adding a New MCP Integration

Questions to ask:
- What service? (Calendar, Notion, database, etc.)
- What operations needed? (read, write, both)
- Which groups should have access?

Implementation:
1. Add MCP server config to the container settings in `src/slimclaw/container_runner.py` (see how MCP servers are mounted via the settings.json)
2. Document available tools in `groups/global/CLAUDE.md` or the specific group's `CLAUDE.md`

### Changing Assistant Behavior

Questions to ask:
- What aspect? (name, trigger, persona, response style)
- Apply to all groups or specific ones?

Simple changes -> edit `src/slimclaw/config.py`:
```python
ASSISTANT_NAME = "NewName"    # Changes trigger to @NewName
```

Persona changes -> edit `groups/global/CLAUDE.md`
Per-group behavior -> edit specific group's `groups/{folder}/CLAUDE.md`

### Adding New Commands

Questions to ask:
- What should the command do?
- Available in all groups or main only?
- Does it need new MCP tools?

Implementation:
1. Commands are handled by the agent naturally — add instructions to `groups/global/CLAUDE.md` or the group's `CLAUDE.md`
2. For trigger-level routing changes, modify `_process_group_messages()` in `src/slimclaw/main.py`

### Changing Deployment

Questions to ask:
- Target platform? (Linux server, Docker, different Mac)
- Service manager? (systemd, launchd, Docker, supervisord)

Implementation:
1. Create appropriate service files
2. Update paths in config
3. Provide setup instructions

## After Changes

Always tell the user:
```bash
# Reinstall and restart
pip install -e .
launchctl unload ~/Library/LaunchAgents/com.slimclaw.plist  # macOS
launchctl load ~/Library/LaunchAgents/com.slimclaw.plist

# Or on Linux:
systemctl --user restart slimclaw
```

## Example Interaction

User: "Add Telegram as an input channel"

1. Ask: "Should Telegram use the same @TARS trigger, or a different one?"
2. Ask: "Should Telegram messages create separate conversation contexts, or share with WhatsApp groups?"
3. Create `src/slimclaw/channels/telegram.py` implementing the `Channel` protocol (see `src/slimclaw/types.py` and `src/slimclaw/channels/whatsapp.py`)
4. Add the channel to `run()` in `src/slimclaw/main.py`
5. Tell user how to authenticate and test
