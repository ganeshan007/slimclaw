from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

from croniter import croniter

from slimclaw.config import DATA_DIR, IPC_POLL_INTERVAL, MAIN_GROUP_FOLDER, TIMEZONE
from slimclaw.container_runner import AvailableGroup
from slimclaw.db import create_task, delete_registered_group, delete_task, get_task_by_id, update_task
from slimclaw.logger import logger
from slimclaw.types import RegisteredGroup, ScheduledTask


class IpcDeps(Protocol):
    async def send_message(self, jid: str, text: str) -> None: ...
    def registered_groups(self) -> dict[str, RegisteredGroup]: ...
    def register_group(self, jid: str, group: RegisteredGroup) -> None: ...
    def unregister_group(self, jid: str) -> bool: ...
    async def sync_group_metadata(self, force: bool) -> None: ...
    def get_available_groups(self) -> list[AvailableGroup]: ...
    def write_groups_snapshot(
        self,
        group_folder: str,
        is_main: bool,
        available_groups: list[AvailableGroup],
        registered_jids: set[str],
    ) -> None: ...


_ipc_watcher_running = False


async def start_ipc_watcher(deps: IpcDeps) -> None:
    global _ipc_watcher_running
    if _ipc_watcher_running:
        logger.debug("IPC watcher already running, skipping duplicate start")
        return
    _ipc_watcher_running = True

    ipc_base_dir = DATA_DIR / "ipc"
    ipc_base_dir.mkdir(parents=True, exist_ok=True)

    logger.info("IPC watcher started (per-group namespaces)")

    while True:
        try:
            group_folders = [
                f
                for f in ipc_base_dir.iterdir()
                if f.is_dir() and f.name != "errors"
            ]
        except Exception as err:
            logger.error("Error reading IPC base directory", error=str(err))
            await asyncio.sleep(IPC_POLL_INTERVAL)
            continue

        registered_groups = deps.registered_groups()

        for source_dir in group_folders:
            source_group = source_dir.name
            is_main = source_group == MAIN_GROUP_FOLDER
            messages_dir = source_dir / "messages"
            tasks_dir = source_dir / "tasks"

            # Process messages
            try:
                if messages_dir.exists():
                    message_files = sorted(
                        f for f in messages_dir.iterdir() if f.suffix == ".json"
                    )
                    for file_path in message_files:
                        try:
                            data = json.loads(file_path.read_text(encoding="utf-8"))
                            if (
                                data.get("type") == "message"
                                and data.get("chatJid")
                                and data.get("text")
                            ):
                                target_group = registered_groups.get(data["chatJid"])
                                if is_main or (
                                    target_group
                                    and target_group.folder == source_group
                                ):
                                    await deps.send_message(
                                        data["chatJid"], data["text"]
                                    )
                                    logger.info(
                                        "IPC message sent",
                                        chat_jid=data["chatJid"],
                                        source_group=source_group,
                                    )
                                else:
                                    logger.warning(
                                        "Unauthorized IPC message attempt blocked",
                                        chat_jid=data["chatJid"],
                                        source_group=source_group,
                                    )
                            file_path.unlink()
                        except Exception as err:
                            logger.error(
                                "Error processing IPC message",
                                file=file_path.name,
                                source_group=source_group,
                                error=str(err),
                            )
                            error_dir = ipc_base_dir / "errors"
                            error_dir.mkdir(parents=True, exist_ok=True)
                            file_path.rename(
                                error_dir / f"{source_group}-{file_path.name}"
                            )
            except Exception as err:
                logger.error(
                    "Error reading IPC messages directory",
                    source_group=source_group,
                    error=str(err),
                )

            # Process tasks
            try:
                if tasks_dir.exists():
                    task_files = sorted(
                        f for f in tasks_dir.iterdir() if f.suffix == ".json"
                    )
                    for file_path in task_files:
                        try:
                            data = json.loads(file_path.read_text(encoding="utf-8"))
                            await process_task_ipc(
                                data, source_group, is_main, deps
                            )
                            file_path.unlink()
                        except Exception as err:
                            logger.error(
                                "Error processing IPC task",
                                file=file_path.name,
                                source_group=source_group,
                                error=str(err),
                            )
                            error_dir = ipc_base_dir / "errors"
                            error_dir.mkdir(parents=True, exist_ok=True)
                            file_path.rename(
                                error_dir / f"{source_group}-{file_path.name}"
                            )
            except Exception as err:
                logger.error(
                    "Error reading IPC tasks directory",
                    source_group=source_group,
                    error=str(err),
                )

        await asyncio.sleep(IPC_POLL_INTERVAL)


async def process_task_ipc(
    data: dict,
    source_group: str,
    is_main: bool,
    deps: IpcDeps,
) -> None:
    registered_groups = deps.registered_groups()
    task_type = data.get("type")

    if task_type == "schedule_task":
        prompt = data.get("prompt")
        schedule_type = data.get("schedule_type")
        schedule_value = data.get("schedule_value")
        target_jid = data.get("targetJid")

        if prompt and schedule_type and schedule_value and target_jid:
            target_group_entry = registered_groups.get(target_jid)
            if not target_group_entry:
                logger.warning(
                    "Cannot schedule task: target group not registered",
                    target_jid=target_jid,
                )
                return

            target_folder = target_group_entry.folder

            if not is_main and target_folder != source_group:
                logger.warning(
                    "Unauthorized schedule_task attempt blocked",
                    source_group=source_group,
                    target_folder=target_folder,
                )
                return

            next_run: Optional[str] = None
            if schedule_type == "cron":
                try:
                    cron = croniter(schedule_value)
                    next_run = datetime.fromtimestamp(
                        cron.get_next(float), tz=timezone.utc
                    ).isoformat()
                except Exception:
                    logger.warning(
                        "Invalid cron expression", schedule_value=schedule_value
                    )
                    return
            elif schedule_type == "interval":
                try:
                    ms = int(schedule_value)
                    if ms <= 0:
                        raise ValueError("must be positive")
                    next_run = datetime.fromtimestamp(
                        time.time() + ms / 1000, tz=timezone.utc
                    ).isoformat()
                except (ValueError, TypeError):
                    logger.warning("Invalid interval", schedule_value=schedule_value)
                    return
            elif schedule_type == "once":
                try:
                    scheduled = datetime.fromisoformat(schedule_value)
                    next_run = scheduled.isoformat()
                except ValueError:
                    logger.warning("Invalid timestamp", schedule_value=schedule_value)
                    return

            task_id = f"task-{int(time.time() * 1000)}-{hex(id(data))[-6:]}"
            context_mode = data.get("context_mode", "isolated")
            if context_mode not in ("group", "isolated"):
                context_mode = "isolated"

            create_task(
                ScheduledTask(
                    id=task_id,
                    group_folder=target_folder,
                    chat_jid=target_jid,
                    prompt=prompt,
                    schedule_type=schedule_type,
                    schedule_value=schedule_value,
                    context_mode=context_mode,
                    next_run=next_run,
                    last_run=None,
                    last_result=None,
                    status="active",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            logger.info(
                "Task created via IPC",
                task_id=task_id,
                source_group=source_group,
                target_folder=target_folder,
                context_mode=context_mode,
            )

    elif task_type == "pause_task":
        task_id = data.get("taskId")
        if task_id:
            task = get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                update_task(task_id, status="paused")
                logger.info("Task paused via IPC", task_id=task_id, source_group=source_group)
            else:
                logger.warning("Unauthorized task pause attempt", task_id=task_id, source_group=source_group)

    elif task_type == "resume_task":
        task_id = data.get("taskId")
        if task_id:
            task = get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                update_task(task_id, status="active")
                logger.info("Task resumed via IPC", task_id=task_id, source_group=source_group)
            else:
                logger.warning("Unauthorized task resume attempt", task_id=task_id, source_group=source_group)

    elif task_type == "cancel_task":
        task_id = data.get("taskId")
        if task_id:
            task = get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                delete_task(task_id)
                logger.info("Task cancelled via IPC", task_id=task_id, source_group=source_group)
            else:
                logger.warning("Unauthorized task cancel attempt", task_id=task_id, source_group=source_group)

    elif task_type == "refresh_groups":
        if is_main:
            logger.info("Group metadata refresh requested via IPC", source_group=source_group)
            await deps.sync_group_metadata(True)
            available_groups = deps.get_available_groups()
            deps.write_groups_snapshot(
                source_group,
                True,
                available_groups,
                set(registered_groups.keys()),
            )
        else:
            logger.warning("Unauthorized refresh_groups attempt blocked", source_group=source_group)

    elif task_type == "register_group":
        if not is_main:
            logger.warning("Unauthorized register_group attempt blocked", source_group=source_group)
            return
        jid = data.get("jid")
        name = data.get("name")
        folder = data.get("folder")
        trigger = data.get("trigger")
        if jid and name and folder and trigger:
            deps.register_group(
                jid,
                RegisteredGroup(
                    name=name,
                    folder=folder,
                    trigger=trigger,
                    added_at=datetime.now(timezone.utc).isoformat(),
                    container_config=data.get("containerConfig"),
                    requires_trigger=data.get("requiresTrigger"),
                ),
            )
        else:
            logger.warning("Invalid register_group request - missing required fields", data=data)

    elif task_type == "unregister_group":
        if not is_main:
            logger.warning("Unauthorized unregister_group attempt blocked", source_group=source_group)
            return
        jid = data.get("jid")
        if jid:
            if jid in registered_groups:
                group = registered_groups[jid]
                if group.folder == MAIN_GROUP_FOLDER:
                    logger.warning("Cannot unregister the main group", jid=jid)
                    return
                deleted = deps.unregister_group(jid)
                if deleted:
                    logger.info("Group unregistered via IPC", jid=jid, name=group.name)
                else:
                    logger.warning("Group not found in database during unregister", jid=jid)
            else:
                logger.warning("Cannot unregister: group not registered", jid=jid)
        else:
            logger.warning("Invalid unregister_group request - missing jid", data=data)

    else:
        logger.warning("Unknown IPC task type", type=task_type)
