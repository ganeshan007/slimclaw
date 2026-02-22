from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from slimclaw.types import Channel, NewMessage


def escape_xml(s: str) -> str:
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def format_messages(messages: list[NewMessage]) -> str:
    lines = [
        f'<message sender="{escape_xml(m.sender_name)}" time="{m.timestamp}">'
        f"{escape_xml(m.content)}</message>"
        for m in messages
    ]
    return f"<messages>\n{chr(10).join(lines)}\n</messages>"


_INTERNAL_TAG_RE = re.compile(r"<internal>[\s\S]*?</internal>")


def strip_internal_tags(text: str) -> str:
    return _INTERNAL_TAG_RE.sub("", text).strip()


def format_outbound(raw_text: str) -> str:
    text = strip_internal_tags(raw_text)
    if not text:
        return ""
    return text


async def route_outbound(channels: list[Channel], jid: str, text: str) -> None:
    channel = next((c for c in channels if c.owns_jid(jid) and c.is_connected()), None)
    if channel is None:
        raise RuntimeError(f"No channel for JID: {jid}")
    await channel.send_message(jid, text)


def find_channel(channels: list[Channel], jid: str) -> Optional[Channel]:
    return next((c for c in channels if c.owns_jid(jid)), None)
