"""WhatsApp channel using neonize (Go-based WhatsApp library with Python bindings)."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from slimclaw.config import ASSISTANT_HAS_OWN_NUMBER, ASSISTANT_NAME, STORE_DIR, TRIGGER_PATTERN
from slimclaw.db import get_last_group_sync, set_last_group_sync, update_chat_name
from slimclaw.logger import logger
from slimclaw.types import NewMessage, OnChatMetadata, OnInboundMessage, OnUnregisteredTrigger, RegisteredGroup

GROUP_SYNC_INTERVAL_MS = 24 * 60 * 60 * 1000  # 24 hours

try:
    from neonize.client import NewClient
    from neonize.events import (
        ConnectedEv,
        DisconnectedEv,
        MessageEv,
        PairStatusEv,
        QREv,
    )
    from neonize.proto.Neonize_pb2 import Message as NeonizeMessage
    from neonize.utils.enum import ReceiptType

    NEONIZE_AVAILABLE = True
except ImportError:
    NEONIZE_AVAILABLE = False


class WhatsAppChannelOpts:
    def __init__(
        self,
        on_message: OnInboundMessage,
        on_chat_metadata: OnChatMetadata,
        registered_groups: callable,
        on_unregistered_trigger: OnUnregisteredTrigger | None = None,
    ):
        self.on_message = on_message
        self.on_chat_metadata = on_chat_metadata
        self.registered_groups = registered_groups
        self.on_unregistered_trigger = on_unregistered_trigger


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(self, opts: WhatsAppChannelOpts) -> None:
        self.opts = opts
        self._connected = False
        self._client: Optional[NewClient] = None
        self._outgoing_queue: list[dict[str, str]] = []
        self._flushing = False
        self._group_sync_timer_started = False
        self._lid_to_phone_map: dict[str, str] = {}

    async def connect(self) -> None:
        if not NEONIZE_AVAILABLE:
            logger.error(
                "neonize not installed. Install with: pip install neonize"
            )
            raise ImportError("neonize is required for WhatsApp support")

        auth_dir = STORE_DIR / "auth"
        auth_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(auth_dir / "neonize.db")

        self._client = NewClient(db_path)
        client = self._client

        # Capture the asyncio event loop for cross-thread scheduling
        # (neonize callbacks run in a Go thread, not the asyncio thread)
        self._loop = asyncio.get_running_loop()
        connected_event = asyncio.Event()

        @client.event(ConnectedEv)
        def on_connected(client_ref, event):
            self._connected = True
            logger.info("Connected to WhatsApp")

            # Schedule async tasks on the main event loop from this Go thread
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._flush_outgoing_queue(), loop=self._loop)
            )
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.sync_group_metadata(), loop=self._loop)
            )

            # Set up periodic sync
            if not self._group_sync_timer_started:
                self._group_sync_timer_started = True

                async def _periodic_sync():
                    while True:
                        await asyncio.sleep(GROUP_SYNC_INTERVAL_MS / 1000)
                        try:
                            await self.sync_group_metadata()
                        except Exception as err:
                            logger.error("Periodic group sync failed", error=str(err))

                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(_periodic_sync(), loop=self._loop)
                )

            self._loop.call_soon_threadsafe(connected_event.set)

        @client.event(DisconnectedEv)
        def on_disconnected(client_ref, event):
            self._connected = False
            logger.info("Disconnected from WhatsApp")

        # Suppress neonize's default QR terminal output during normal operation.
        # neonize has TWO QR paths: client.qr() (segno terminal print) and
        # @client.event(QREv) (Python event). Override both.
        @client.qr
        def on_qr_raw(client_ref, data_qr):
            logger.warning(
                "WhatsApp requesting QR authentication — run slimclaw-auth to re-authenticate"
            )

        @client.event(QREv)
        def on_qr_event(client_ref, event):
            pass  # Handled by on_qr_raw above

        @client.event(MessageEv)
        def on_message(client_ref, event):
            # neonize callbacks run in a Go thread — dispatch to the asyncio
            # thread so SQLite and other state are accessed safely.
            self._loop.call_soon_threadsafe(self._handle_message_safe, event)

        # Start client in a background thread (neonize is synchronous Go under the hood).
        # client.connect() blocks forever (it's the Go event loop), so we fire-and-forget
        # the executor and only await the connected_event signal from the callback.
        self._loop.run_in_executor(None, client.connect)
        await asyncio.wait_for(connected_event.wait(), timeout=30)

    @staticmethod
    def _jid_to_string(jid_obj) -> str:
        """Convert a neonize JID protobuf to a standard string like '12345@g.us'."""
        if isinstance(jid_obj, str):
            return jid_obj
        user = getattr(jid_obj, "User", "")
        server = getattr(jid_obj, "Server", "")
        if user and server:
            return f"{user}@{server}"
        return str(jid_obj)

    def _handle_message_safe(self, event) -> None:
        """Wrapper called on the asyncio thread via call_soon_threadsafe."""
        try:
            self._handle_message(event)
        except Exception as err:
            logger.error("Error handling WhatsApp message", error=str(err))

    def _handle_message(self, event) -> None:
        msg = event.Message
        info = event.Info

        chat_jid_obj = info.MessageSource.Chat
        chat_jid = self._jid_to_string(chat_jid_obj)
        logger.debug("WA message event", chat_jid=chat_jid)
        if not chat_jid or chat_jid == "status@broadcast":
            return

        from datetime import datetime, timezone

        timestamp_epoch = int(info.Timestamp)
        # neonize may return milliseconds instead of seconds — normalize
        if timestamp_epoch > 1e12:
            timestamp_epoch = timestamp_epoch // 1000
        # Guard against absurd timestamps from history sync
        if timestamp_epoch > 4102444800 or timestamp_epoch <= 0:  # > year 2100 or <= 0
            logger.debug("Skipping message with bad timestamp", epoch=timestamp_epoch, chat_jid=chat_jid)
            return
        timestamp = datetime.fromtimestamp(timestamp_epoch, tz=timezone.utc).isoformat()

        is_group = chat_jid.endswith("@g.us")
        self.opts.on_chat_metadata(chat_jid, timestamp, None, "whatsapp", is_group)

        # Extract text content early — needed for both registered and unregistered handling
        content = ""
        if msg.conversation:
            content = msg.conversation
        elif msg.extendedTextMessage and msg.extendedTextMessage.text:
            content = msg.extendedTextMessage.text
        elif msg.imageMessage and msg.imageMessage.caption:
            content = msg.imageMessage.caption
        elif msg.videoMessage and msg.videoMessage.caption:
            content = msg.videoMessage.caption

        groups = self.opts.registered_groups()
        if chat_jid not in groups:
            # Check if someone is trying to invoke the bot in an unregistered group
            if (
                content
                and is_group
                and self.opts.on_unregistered_trigger
                and TRIGGER_PATTERN.search(content.strip())
            ):
                sender_obj = info.MessageSource.Sender
                sender = self._jid_to_string(sender_obj) if sender_obj else chat_jid
                try:
                    push_name = getattr(info, "PushName", None) or getattr(info, "Pushname", None)
                    sender_name = str(push_name) if push_name else sender.split("@")[0]
                except Exception:
                    sender_name = sender.split("@")[0]
                self.opts.on_unregistered_trigger(chat_jid, sender_name, content)
            return

        if not content:
            return

        sender_obj = info.MessageSource.Sender
        sender = self._jid_to_string(sender_obj) if sender_obj else chat_jid
        try:
            push_name = getattr(info, "PushName", None) or getattr(info, "Pushname", None)
            sender_name = str(push_name) if push_name else sender.split("@")[0]
        except Exception:
            sender_name = sender.split("@")[0]
        from_me = bool(info.MessageSource.IsFromMe)

        is_bot_message = (
            from_me
            if ASSISTANT_HAS_OWN_NUMBER
            else content.startswith(f"{ASSISTANT_NAME}:")
        )

        self.opts.on_message(
            chat_jid,
            NewMessage(
                id=str(info.ID),
                chat_jid=chat_jid,
                sender=sender,
                sender_name=sender_name,
                content=content,
                timestamp=timestamp,
                is_from_me=from_me,
                is_bot_message=is_bot_message,
            ),
        )

    async def send_message(self, jid: str, text: str) -> None:
        prefixed = text if ASSISTANT_HAS_OWN_NUMBER else f"{ASSISTANT_NAME}: {text}"

        if not self._connected or not self._client:
            self._outgoing_queue.append({"jid": jid, "text": prefixed})
            logger.info(
                "WA disconnected, message queued",
                jid=jid,
                length=len(prefixed),
                queue_size=len(self._outgoing_queue),
            )
            return

        try:
            target_jid = self._parse_jid(jid)
            await self._loop.run_in_executor(
                None,
                lambda: self._client.send_message(target_jid, prefixed),
            )
            logger.info("Message sent", jid=jid, length=len(prefixed))
        except Exception as err:
            self._outgoing_queue.append({"jid": jid, "text": prefixed})
            logger.warning(
                "Failed to send, message queued",
                jid=jid,
                error=str(err),
                queue_size=len(self._outgoing_queue),
            )

    def _parse_jid(self, jid_str: str):
        """Parse a JID string into neonize JID protobuf."""
        from neonize.proto.Neonize_pb2 import JID

        parts = jid_str.split("@")
        user = parts[0]
        server = parts[1] if len(parts) > 1 else "s.whatsapp.net"
        return JID(User=user, Server=server, RawAgent=0, Device=0, Integrator=0)

    def is_connected(self) -> bool:
        return self._connected

    def owns_jid(self, jid: str) -> bool:
        return jid.endswith("@g.us") or jid.endswith("@s.whatsapp.net")

    async def disconnect(self) -> None:
        self._connected = False
        if self._client and self._loop:
            try:
                await self._loop.run_in_executor(None, self._client.disconnect)
            except Exception:
                pass

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        if not self._client or not self._connected:
            return
        try:
            target_jid = self._parse_jid(jid)
            if is_typing:
                await self._loop.run_in_executor(
                    None, lambda: self._client.send_chat_presence(target_jid, "composing", "")
                )
            else:
                await self._loop.run_in_executor(
                    None, lambda: self._client.send_chat_presence(target_jid, "paused", "")
                )
        except Exception as err:
            logger.debug("Failed to update typing status", jid=jid, error=str(err))

    async def sync_group_metadata(self, force: bool = False) -> None:
        if not force:
            last_sync = get_last_group_sync()
            if last_sync:
                from datetime import datetime

                last_sync_time = datetime.fromisoformat(last_sync).timestamp() * 1000
                if time.time() * 1000 - last_sync_time < GROUP_SYNC_INTERVAL_MS:
                    logger.debug("Skipping group sync - synced recently", last_sync=last_sync)
                    return

        if not self._client or not self._connected:
            return

        try:
            logger.info("Syncing group metadata from WhatsApp...")
            groups = await self._loop.run_in_executor(
                None, self._client.get_joined_groups
            )

            count = 0
            if groups:
                for group_info in groups:
                    jid_obj = group_info.JID
                    jid = self._jid_to_string(jid_obj)
                    name = str(group_info.GroupName.Name) if hasattr(group_info, 'GroupName') and group_info.GroupName and group_info.GroupName.Name else None
                    if jid and name:
                        update_chat_name(jid, name)
                        count += 1

            set_last_group_sync()
            logger.info("Group metadata synced", count=count)
        except Exception as err:
            logger.error("Failed to sync group metadata", error=str(err))

    async def _flush_outgoing_queue(self) -> None:
        if self._flushing or not self._outgoing_queue:
            return
        self._flushing = True
        try:
            logger.info("Flushing outgoing message queue", count=len(self._outgoing_queue))
            while self._outgoing_queue:
                item = self._outgoing_queue.pop(0)
                try:
                    target_jid = self._parse_jid(item["jid"])
                    await self._loop.run_in_executor(
                        None,
                        lambda: self._client.send_message(target_jid, item["text"]),
                    )
                    logger.info("Queued message sent", jid=item["jid"], length=len(item["text"]))
                except Exception as err:
                    logger.warning("Failed to flush queued message", error=str(err))
                    self._outgoing_queue.insert(0, item)
                    break
        finally:
            self._flushing = False
