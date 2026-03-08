---
name: add-app-template
description: Template and guide for adding any new app (Telegram, Discord, Slack, Signal, etc.) to SlimClaw. Follow this to create a single-file integration that auto-discovers without touching main.py.
---

# Adding a New App to SlimClaw

This guide teaches you how to add support for **any** messaging app by creating a single Python file. No changes to `main.py` or core code are needed — the app registry auto-discovers your channel.

## How It Works

1. You create `src/slimclaw/channels/{app_name}.py`
2. The registry (`channels/registry.py`) scans the `channels/` package at startup
3. Your class is found if `class.name == module filename`
4. `main.py` instantiates it with `AppOpts` and calls `connect()`

## The Channel Protocol (6 methods)

Your class must satisfy the `Channel` protocol defined in `src/slimclaw/types.py`:

```python
class Channel(Protocol):
    name: str

    async def connect(self) -> None: ...
    async def send_message(self, jid: str, text: str) -> None: ...
    def is_connected(self) -> bool: ...
    def owns_jid(self, jid: str) -> bool: ...
    async def disconnect(self) -> None: ...
    async def set_typing(self, jid: str, is_typing: bool) -> None: ...
```

## JID Prefix Convention

Each app owns a JID prefix so the router knows which channel handles which chat:

| App | Prefix | Example JID | `owns_jid` check |
|-----|--------|-------------|-------------------|
| whatsapp | (legacy) | `12345@g.us` | `endswith("@g.us") or endswith("@s.whatsapp.net")` |
| telegram | `tg:` | `tg:123456789` | `startswith("tg:")` |
| discord | `dc:` | `dc:1234567890` | `startswith("dc:")` |
| slack | `sl:` | `sl:C1234567890` | `startswith("sl:")` |
| signal | `sg:` | `sg:+14155551234` | `startswith("sg:")` |

For a new app, pick a 2-letter prefix that doesn't conflict.

## Template

Create `src/slimclaw/channels/{app_name}.py`:

```python
"""SlimClaw {App Name} channel."""
from __future__ import annotations

import asyncio
from typing import Optional

from slimclaw.config import ASSISTANT_NAME, TRIGGER_PATTERN
from slimclaw.logger import logger
from slimclaw.types import AppOpts, NewMessage

# Guard optional dependency
try:
    import some_library  # The app's SDK
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

PREFIX = "{prefix}:"  # e.g. "tg:", "dc:", "sl:"


class {AppName}Channel:
    name = "{app_name}"  # MUST match the module filename

    def __init__(self, opts: AppOpts) -> None:
        self.opts = opts
        self._connected = False
        self._client = None

    async def connect(self) -> None:
        if not AVAILABLE:
            raise ImportError("{library} is required for {App Name} support")

        # Read credentials from .env at connect time, never at import time
        from slimclaw.env import read_env_file
        env = read_env_file(["{APP_NAME}_BOT_TOKEN"])
        token = env.get("{APP_NAME}_BOT_TOKEN")
        if not token:
            raise RuntimeError("{APP_NAME}_BOT_TOKEN not set in .env")

        # Initialize client and start listening
        # self._client = ...
        # Set up message handler that calls self._handle_message()
        self._connected = True
        logger.info("{App Name} connected")

    def _handle_message(self, chat_id: str, sender_name: str, text: str, message_id: str, timestamp: str) -> None:
        """Called when a message arrives. Must run on the asyncio thread."""
        jid = f"{PREFIX}{chat_id}"

        # Report chat metadata
        is_group = ...  # True if this is a group chat
        self.opts.on_chat_metadata(jid, timestamp, None, self.name, is_group)

        # Check registration
        groups = self.opts.registered_groups()
        if jid not in groups:
            if (
                text
                and is_group
                and self.opts.on_unregistered_trigger
                and TRIGGER_PATTERN.search(text.strip())
            ):
                self.opts.on_unregistered_trigger(jid, sender_name, text)
            return

        if not text:
            return

        # Store the message
        self.opts.on_message(
            jid,
            NewMessage(
                id=message_id,
                chat_jid=jid,
                sender=f"{PREFIX}{sender_name}",
                sender_name=sender_name,
                content=text,
                timestamp=timestamp,
            ),
        )

    async def send_message(self, jid: str, text: str) -> None:
        chat_id = jid.removeprefix(PREFIX)
        # Send via your app's API
        logger.info("Message sent", jid=jid, length=len(text))

    def is_connected(self) -> bool:
        return self._connected

    def owns_jid(self, jid: str) -> bool:
        return jid.startswith(PREFIX)

    async def disconnect(self) -> None:
        self._connected = False
        # Clean up client

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        # Optional: send typing indicator via your app's API
        pass
```

## Message Handler Pattern

The handler must follow this exact sequence:

1. **`on_chat_metadata(jid, timestamp, name, channel_name, is_group)`** — always, even for unregistered chats
2. **Registration check** — `if jid not in self.opts.registered_groups(): return`
3. **Unregistered trigger** — before returning, check if trigger word was used and notify via `on_unregistered_trigger`
4. **`on_message(jid, NewMessage(...))`** — store the message for processing

## Credential Handling

- Read secrets in `connect()`, never at import time
- Use `read_env_file()` from `slimclaw.env`
- Token env var convention: `{APP_NAME}_BOT_TOKEN`
- This ensures the registry can import your module without credentials being present

## Optional Dependencies

Add your app's SDK as an optional dependency in `pyproject.toml`:

```toml
[project.optional-dependencies]
telegram = ["python-telegram-bot>=20"]
discord = ["discord.py>=2"]
slack = ["slack-sdk>=3"]
```

Users install with: `pip install slimclaw[telegram]`

Guard the import in your channel file:
```python
try:
    import telegram
    AVAILABLE = True
except ImportError:
    AVAILABLE = False
```

The registry silently skips modules that fail to import.

## Auto-Discovery Checklist

For your app to be auto-discovered:

- [ ] File is in `src/slimclaw/channels/` (not in a subdirectory)
- [ ] Module name doesn't start with `_`
- [ ] Class has `name` attribute matching the module filename exactly
- [ ] Module can be imported (deps installed or guarded with try/except)
- [ ] No changes needed to `main.py`, `registry.py`, or `__init__.py`

## ENABLED_APPS

Users can optionally restrict which apps load via `.env`:

```
ENABLED_APPS=whatsapp,telegram
```

When unset, all discovered apps load (backwards-compatible default).

## Testing

After creating your channel file:

```bash
# Verify discovery
python3 -c "from slimclaw.channels.registry import discover_apps; print(discover_apps())"

# Run existing tests (should still pass)
pytest

# Run the app
slimclaw
```

## Reference Implementation

See `src/slimclaw/channels/whatsapp.py` for the full reference implementation.
