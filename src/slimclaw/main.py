"""SlimClaw main orchestrator — Python port of src/index.ts"""
from __future__ import annotations

import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Optional

from slimclaw.config import (
    ASSISTANT_NAME,
    DATA_DIR,
    GROUPS_DIR,
    IDLE_TIMEOUT,
    MAIN_GROUP_FOLDER,
    POLL_INTERVAL,
    TRIGGER_PATTERN,
)
from slimclaw.channels.whatsapp import WhatsAppChannel, WhatsAppChannelOpts
from slimclaw.container_runner import (
    AvailableGroup,
    ContainerInput,
    ContainerOutput,
    run_container_agent,
    write_groups_snapshot,
    write_tasks_snapshot,
)
from slimclaw.container_runtime import cleanup_orphans, ensure_container_runtime_running
from slimclaw.db import (
    get_all_chats,
    get_all_registered_groups,
    get_all_sessions,
    get_all_tasks,
    get_messages_since,
    get_new_messages,
    get_router_state,
    init_database,
    set_registered_group,
    set_router_state,
    set_session,
    store_chat_metadata,
    store_message,
)
from slimclaw.group_queue import GroupQueue
from slimclaw.ipc import start_ipc_watcher
from slimclaw.logger import logger
from slimclaw.router import find_channel, format_messages, format_outbound
from slimclaw.task_scheduler import start_scheduler_loop
from slimclaw.types import Channel, NewMessage, RegisteredGroup

# Module state
_last_timestamp: str = ""
_sessions: dict[str, str] = {}
_registered_groups: dict[str, RegisteredGroup] = {}
_last_agent_timestamp: dict[str, str] = {}
_message_loop_running: bool = False

_channels: list[Channel] = []
_queue = GroupQueue()
_whatsapp: Optional[WhatsAppChannel] = None


def _load_state() -> None:
    global _last_timestamp, _sessions, _registered_groups, _last_agent_timestamp

    _last_timestamp = get_router_state("last_timestamp") or ""
    agent_ts = get_router_state("last_agent_timestamp")
    try:
        _last_agent_timestamp = json.loads(agent_ts) if agent_ts else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupted last_agent_timestamp in DB, resetting")
        _last_agent_timestamp = {}
    _sessions = get_all_sessions()
    _registered_groups = get_all_registered_groups()
    logger.info("State loaded", group_count=len(_registered_groups))


def _save_state() -> None:
    set_router_state("last_timestamp", _last_timestamp)
    set_router_state("last_agent_timestamp", json.dumps(_last_agent_timestamp))


def _register_group(jid: str, group: RegisteredGroup) -> None:
    _registered_groups[jid] = group
    set_registered_group(jid, group)

    group_dir = GROUPS_DIR / group.folder
    (group_dir / "logs").mkdir(parents=True, exist_ok=True)

    logger.info("Group registered", jid=jid, name=group.name, folder=group.folder)


def _get_available_groups() -> list[AvailableGroup]:
    chats = get_all_chats()
    registered_jids = set(_registered_groups.keys())

    return [
        AvailableGroup(
            jid=c.jid,
            name=c.name,
            last_activity=c.last_message_time,
            is_registered=c.jid in registered_jids,
        )
        for c in chats
        if c.jid != "__group_sync__" and c.is_group
    ]


def _on_unregistered_trigger(chat_jid: str, sender_name: str, content: str) -> None:
    """Called when someone uses the trigger word in an unregistered group."""
    # Look up group name from chats table
    chats = get_all_chats()
    group_name = chat_jid
    for c in chats:
        if c.jid == chat_jid:
            group_name = c.name or chat_jid
            break

    logger.info("Trigger in unregistered group", chat_jid=chat_jid, group=group_name, sender=sender_name)

    # Find the main channel JID to send notification
    main_jid = None
    for jid, group in _registered_groups.items():
        if group.folder == MAIN_GROUP_FOLDER:
            main_jid = jid
            break

    if main_jid and _channels:
        channel = find_channel(_channels, main_jid)
        if channel:
            msg = (
                f"{ASSISTANT_NAME}: {sender_name} mentioned @{ASSISTANT_NAME} in "
                f"*{group_name}* (not registered).\n\n"
                f"To add this group, say: *join {group_name}*"
            )
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(channel.send_message(main_jid, msg))
                )
            except Exception as err:
                logger.error("Failed to notify main channel", error=str(err))


async def _process_group_messages(chat_jid: str) -> bool:
    """Process all pending messages for a group.
    Called by the GroupQueue when it's this group's turn.
    """
    global _last_agent_timestamp

    group = _registered_groups.get(chat_jid)
    if not group:
        return True

    channel = find_channel(_channels, chat_jid)
    if not channel:
        logger.warning(f"No channel owns JID {chat_jid}, skipping messages")
        return True

    is_main_group = group.folder == MAIN_GROUP_FOLDER

    since_timestamp = _last_agent_timestamp.get(chat_jid, "")
    missed_messages = get_messages_since(chat_jid, since_timestamp, ASSISTANT_NAME)

    if not missed_messages:
        return True

    # For non-main groups, check if trigger is required and present
    if not is_main_group and group.requires_trigger is not False:
        has_trigger = any(TRIGGER_PATTERN.search(m.content.strip()) for m in missed_messages)
        if not has_trigger:
            return True

    prompt = format_messages(missed_messages)

    # Advance cursor, save old one for rollback
    previous_cursor = _last_agent_timestamp.get(chat_jid, "")
    _last_agent_timestamp[chat_jid] = missed_messages[-1].timestamp
    _save_state()

    logger.info("Processing messages", group=group.name, message_count=len(missed_messages))

    # Idle timer
    idle_task: Optional[asyncio.Task] = None

    def reset_idle_timer():
        nonlocal idle_task
        if idle_task and not idle_task.done():
            idle_task.cancel()

        async def _idle_close():
            await asyncio.sleep(IDLE_TIMEOUT / 1000)
            logger.debug("Idle timeout, closing container stdin", group=group.name)
            _queue.close_stdin(chat_jid)

        idle_task = asyncio.create_task(_idle_close())

    await channel.set_typing(chat_jid, True)
    had_error = False
    output_sent_to_user = False

    async def on_output(result: ContainerOutput) -> None:
        nonlocal had_error, output_sent_to_user
        if result.result:
            raw = result.result if isinstance(result.result, str) else json.dumps(result.result)
            import re
            text = re.sub(r"<internal>[\s\S]*?</internal>", "", raw).strip()
            logger.info(f"Agent output: {raw[:200]}", group=group.name)
            if text:
                await channel.send_message(chat_jid, text)
                output_sent_to_user = True
            reset_idle_timer()

        if result.status == "error":
            had_error = True

    result = await _run_agent(group, prompt, chat_jid, on_output)

    await channel.set_typing(chat_jid, False)
    if idle_task and not idle_task.done():
        idle_task.cancel()

    if result == "error" or had_error:
        if output_sent_to_user:
            logger.warning(
                "Agent error after output was sent, skipping cursor rollback",
                group=group.name,
            )
            return True
        _last_agent_timestamp[chat_jid] = previous_cursor
        _save_state()
        logger.warning("Agent error, rolled back message cursor for retry", group=group.name)
        return False

    return True


async def _run_agent(
    group: RegisteredGroup,
    prompt: str,
    chat_jid: str,
    on_output: Optional[callable] = None,
) -> str:
    """Run the container agent. Returns 'success' or 'error'."""
    is_main = group.folder == MAIN_GROUP_FOLDER
    session_id = _sessions.get(group.folder)

    # Update tasks snapshot
    tasks = get_all_tasks()
    write_tasks_snapshot(
        group.folder,
        is_main,
        [
            {
                "id": t.id,
                "groupFolder": t.group_folder,
                "prompt": t.prompt,
                "schedule_type": t.schedule_type,
                "schedule_value": t.schedule_value,
                "status": t.status,
                "next_run": t.next_run,
            }
            for t in tasks
        ],
    )

    # Update available groups snapshot
    available_groups = _get_available_groups()
    write_groups_snapshot(
        group.folder,
        is_main,
        available_groups,
        set(_registered_groups.keys()),
    )

    # Wrap onOutput to track session ID
    async def wrapped_on_output(output: ContainerOutput) -> None:
        if output.new_session_id:
            _sessions[group.folder] = output.new_session_id
            set_session(group.folder, output.new_session_id)
        if on_output:
            await on_output(output)

    try:
        output = await run_container_agent(
            group,
            ContainerInput(
                prompt=prompt,
                session_id=session_id,
                group_folder=group.folder,
                chat_jid=chat_jid,
                is_main=is_main,
            ),
            lambda proc, cn: _queue.register_process(chat_jid, proc, cn, group.folder),
            wrapped_on_output if on_output else None,
        )

        if output.new_session_id:
            _sessions[group.folder] = output.new_session_id
            set_session(group.folder, output.new_session_id)

        if output.status == "error":
            logger.error("Container agent error", group=group.name, error=output.error)
            return "error"

        return "success"
    except Exception as err:
        logger.error("Agent error", group=group.name, error=str(err))
        return "error"


async def _start_message_loop() -> None:
    global _last_timestamp, _message_loop_running

    if _message_loop_running:
        logger.debug("Message loop already running, skipping duplicate start")
        return
    _message_loop_running = True

    logger.info(f"SlimClaw running (trigger: @{ASSISTANT_NAME})")

    while True:
        try:
            jids = list(_registered_groups.keys())
            messages, new_timestamp = get_new_messages(jids, _last_timestamp, ASSISTANT_NAME)

            if messages:
                logger.info("New messages", count=len(messages))

                _last_timestamp = new_timestamp
                _save_state()

                # Deduplicate by group
                messages_by_group: dict[str, list[NewMessage]] = {}
                for msg in messages:
                    messages_by_group.setdefault(msg.chat_jid, []).append(msg)

                for chat_jid, group_messages in messages_by_group.items():
                    group = _registered_groups.get(chat_jid)
                    if not group:
                        continue

                    channel = find_channel(_channels, chat_jid)
                    if not channel:
                        logger.warning(f"No channel owns JID {chat_jid}, skipping")
                        continue

                    is_main_group = group.folder == MAIN_GROUP_FOLDER
                    needs_trigger = not is_main_group and group.requires_trigger is not False

                    if needs_trigger:
                        has_trigger = any(
                            TRIGGER_PATTERN.search(m.content.strip()) for m in group_messages
                        )
                        if not has_trigger:
                            continue

                    # Pull all messages since lastAgentTimestamp
                    all_pending = get_messages_since(
                        chat_jid,
                        _last_agent_timestamp.get(chat_jid, ""),
                        ASSISTANT_NAME,
                    )
                    messages_to_send = all_pending if all_pending else group_messages
                    formatted = format_messages(messages_to_send)

                    if _queue.send_message(chat_jid, formatted):
                        logger.debug(
                            "Piped messages to active container",
                            chat_jid=chat_jid,
                            count=len(messages_to_send),
                        )
                        _last_agent_timestamp[chat_jid] = messages_to_send[-1].timestamp
                        _save_state()
                        await channel.set_typing(chat_jid, True)
                    else:
                        _queue.enqueue_message_check(chat_jid)

        except Exception as err:
            logger.error("Error in message loop", error=str(err))

        await asyncio.sleep(POLL_INTERVAL)


def _recover_pending_messages() -> None:
    """Startup recovery: check for unprocessed messages in registered groups."""
    for chat_jid, group in _registered_groups.items():
        since_timestamp = _last_agent_timestamp.get(chat_jid, "")
        pending = get_messages_since(chat_jid, since_timestamp, ASSISTANT_NAME)
        if pending:
            logger.info(
                "Recovery: found unprocessed messages",
                group=group.name,
                pending_count=len(pending),
            )
            _queue.enqueue_message_check(chat_jid)


async def main() -> None:
    global _whatsapp

    ensure_container_runtime_running()
    cleanup_orphans()
    init_database()
    logger.info("Database initialized")
    _load_state()

    # Graceful shutdown
    loop = asyncio.get_event_loop()

    async def shutdown(sig_name: str) -> None:
        logger.info("Shutdown signal received", signal=sig_name)
        await _queue.shutdown(10000)
        for ch in _channels:
            await ch.disconnect()
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s.name)))

    # Channel callbacks
    channel_opts = WhatsAppChannelOpts(
        on_message=lambda chat_jid, msg: store_message(msg),
        on_chat_metadata=lambda chat_jid, ts, name=None, channel=None, is_group=None: store_chat_metadata(
            chat_jid, ts, name, channel, is_group
        ),
        registered_groups=lambda: _registered_groups,
        on_unregistered_trigger=_on_unregistered_trigger,
    )

    # Create and connect channels
    _whatsapp = WhatsAppChannel(channel_opts)
    _channels.append(_whatsapp)
    await _whatsapp.connect()

    # Start subsystems
    # Scheduler dependencies
    class _SchedulerDeps:
        def registered_groups(self):
            return _registered_groups

        def get_sessions(self):
            return _sessions

        @property
        def queue(self):
            return _queue

        def on_process(self, group_jid, proc, container_name, group_folder):
            _queue.register_process(group_jid, proc, container_name, group_folder)

        async def send_message(self, jid, raw_text):
            channel = find_channel(_channels, jid)
            if not channel:
                logger.warning(f"No channel owns JID {jid}, cannot send message")
                return
            text = format_outbound(raw_text)
            if text:
                await channel.send_message(jid, text)

    def _log_task_exception(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.error("Background task failed", error=str(task.exception()))

    scheduler_task = asyncio.create_task(start_scheduler_loop(_SchedulerDeps()))
    scheduler_task.add_done_callback(_log_task_exception)

    # IPC watcher dependencies
    class _IpcDeps:
        async def send_message(self, jid, text):
            channel = find_channel(_channels, jid)
            if not channel:
                raise RuntimeError(f"No channel for JID: {jid}")
            await channel.send_message(jid, text)

        def registered_groups(self):
            return _registered_groups

        def register_group(self, jid, group):
            _register_group(jid, group)

        async def sync_group_metadata(self, force):
            if _whatsapp:
                await _whatsapp.sync_group_metadata(force)

        def get_available_groups(self):
            return _get_available_groups()

        def write_groups_snapshot(self, gf, im, ag, rj):
            write_groups_snapshot(gf, im, ag, rj)

    ipc_task = asyncio.create_task(start_ipc_watcher(_IpcDeps()))
    ipc_task.add_done_callback(_log_task_exception)

    _queue.set_process_messages_fn(_process_group_messages)
    _recover_pending_messages()
    await _start_message_loop()


def run() -> None:
    """Entry point for the slimclaw console script."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
