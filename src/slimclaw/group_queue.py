from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from slimclaw.config import DATA_DIR, MAX_CONCURRENT_CONTAINERS
from slimclaw.logger import logger

MAX_RETRIES = 5
BASE_RETRY_MS = 5000


@dataclass
class QueuedTask:
    id: str
    group_jid: str
    fn: Callable[[], Awaitable[None]]


@dataclass
class GroupState:
    active: bool = False
    pending_messages: bool = False
    pending_tasks: list[QueuedTask] = field(default_factory=list)
    process: Optional[asyncio.subprocess.Process] = None
    container_name: Optional[str] = None
    group_folder: Optional[str] = None
    retry_count: int = 0


class GroupQueue:
    def __init__(self) -> None:
        self._groups: dict[str, GroupState] = {}
        self._active_count = 0
        self._waiting_groups: list[str] = []
        self._process_messages_fn: Optional[Callable[[str], Awaitable[bool]]] = None
        self._shutting_down = False

    def _get_group(self, group_jid: str) -> GroupState:
        state = self._groups.get(group_jid)
        if state is None:
            state = GroupState()
            self._groups[group_jid] = state
        return state

    def set_process_messages_fn(self, fn: Callable[[str], Awaitable[bool]]) -> None:
        self._process_messages_fn = fn

    def enqueue_message_check(self, group_jid: str) -> None:
        if self._shutting_down:
            return

        state = self._get_group(group_jid)

        if state.active:
            state.pending_messages = True
            logger.debug("Container active, message queued", group_jid=group_jid)
            return

        if self._active_count >= MAX_CONCURRENT_CONTAINERS:
            state.pending_messages = True
            if group_jid not in self._waiting_groups:
                self._waiting_groups.append(group_jid)
            logger.debug(
                "At concurrency limit, message queued",
                group_jid=group_jid,
                active_count=self._active_count,
            )
            return

        asyncio.create_task(self._run_for_group(group_jid, "messages"))

    def enqueue_task(
        self, group_jid: str, task_id: str, fn: Callable[[], Awaitable[None]]
    ) -> None:
        if self._shutting_down:
            return

        state = self._get_group(group_jid)

        if any(t.id == task_id for t in state.pending_tasks):
            logger.debug("Task already queued, skipping", group_jid=group_jid, task_id=task_id)
            return

        if state.active:
            state.pending_tasks.append(QueuedTask(id=task_id, group_jid=group_jid, fn=fn))
            logger.debug("Container active, task queued", group_jid=group_jid, task_id=task_id)
            return

        if self._active_count >= MAX_CONCURRENT_CONTAINERS:
            state.pending_tasks.append(QueuedTask(id=task_id, group_jid=group_jid, fn=fn))
            if group_jid not in self._waiting_groups:
                self._waiting_groups.append(group_jid)
            logger.debug(
                "At concurrency limit, task queued",
                group_jid=group_jid,
                task_id=task_id,
                active_count=self._active_count,
            )
            return

        asyncio.create_task(self._run_task(group_jid, QueuedTask(id=task_id, group_jid=group_jid, fn=fn)))

    def register_process(
        self,
        group_jid: str,
        proc: asyncio.subprocess.Process,
        container_name: str,
        group_folder: Optional[str] = None,
    ) -> None:
        state = self._get_group(group_jid)
        state.process = proc
        state.container_name = container_name
        if group_folder:
            state.group_folder = group_folder

    def send_message(self, group_jid: str, text: str) -> bool:
        state = self._get_group(group_jid)
        if not state.active or not state.group_folder:
            return False

        input_dir = DATA_DIR / "ipc" / state.group_folder / "input"
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{int(time.time() * 1000)}-{hex(id(text))[-4:]}.json"
            filepath = input_dir / filename
            temp_path = filepath.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps({"type": "message", "text": text}))
            temp_path.rename(filepath)
            return True
        except Exception:
            return False

    def close_stdin(self, group_jid: str) -> None:
        state = self._get_group(group_jid)
        if not state.active or not state.group_folder:
            return

        input_dir = DATA_DIR / "ipc" / state.group_folder / "input"
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            (input_dir / "_close").write_text("")
        except Exception:
            pass

    async def _run_for_group(self, group_jid: str, reason: str) -> None:
        state = self._get_group(group_jid)
        state.active = True
        state.pending_messages = False
        self._active_count += 1

        logger.debug(
            "Starting container for group",
            group_jid=group_jid,
            reason=reason,
            active_count=self._active_count,
        )

        try:
            if self._process_messages_fn:
                success = await self._process_messages_fn(group_jid)
                if success:
                    state.retry_count = 0
                else:
                    self._schedule_retry(group_jid, state)
        except Exception as err:
            logger.error("Error processing messages for group", group_jid=group_jid, error=str(err))
            self._schedule_retry(group_jid, state)
        finally:
            state.active = False
            state.process = None
            state.container_name = None
            state.group_folder = None
            self._active_count -= 1
            self._drain_group(group_jid)

    async def _run_task(self, group_jid: str, task: QueuedTask) -> None:
        state = self._get_group(group_jid)
        state.active = True
        self._active_count += 1

        logger.debug(
            "Running queued task",
            group_jid=group_jid,
            task_id=task.id,
            active_count=self._active_count,
        )

        try:
            await task.fn()
        except Exception as err:
            logger.error("Error running task", group_jid=group_jid, task_id=task.id, error=str(err))
        finally:
            state.active = False
            state.process = None
            state.container_name = None
            state.group_folder = None
            self._active_count -= 1
            self._drain_group(group_jid)

    def _schedule_retry(self, group_jid: str, state: GroupState) -> None:
        state.retry_count += 1
        if state.retry_count > MAX_RETRIES:
            logger.error(
                "Max retries exceeded, dropping messages (will retry on next incoming message)",
                group_jid=group_jid,
                retry_count=state.retry_count,
            )
            state.retry_count = 0
            return

        delay_ms = BASE_RETRY_MS * math.pow(2, state.retry_count - 1)
        logger.info(
            "Scheduling retry with backoff",
            group_jid=group_jid,
            retry_count=state.retry_count,
            delay_ms=delay_ms,
        )

        async def _retry():
            await asyncio.sleep(delay_ms / 1000)
            if not self._shutting_down:
                self.enqueue_message_check(group_jid)

        asyncio.create_task(_retry())

    def _drain_group(self, group_jid: str) -> None:
        if self._shutting_down:
            return

        state = self._get_group(group_jid)

        # Tasks first
        if state.pending_tasks:
            task = state.pending_tasks.pop(0)
            asyncio.create_task(self._run_task(group_jid, task))
            return

        # Then pending messages
        if state.pending_messages:
            asyncio.create_task(self._run_for_group(group_jid, "drain"))
            return

        # Nothing pending — check waiting groups
        self._drain_waiting()

    def _drain_waiting(self) -> None:
        while (
            self._waiting_groups
            and self._active_count < MAX_CONCURRENT_CONTAINERS
        ):
            next_jid = self._waiting_groups.pop(0)
            state = self._get_group(next_jid)

            if state.pending_tasks:
                task = state.pending_tasks.pop(0)
                asyncio.create_task(self._run_task(next_jid, task))
            elif state.pending_messages:
                asyncio.create_task(self._run_for_group(next_jid, "drain"))

    async def shutdown(self, grace_period_ms: int = 0) -> None:
        self._shutting_down = True

        active_containers: list[str] = []
        for jid, state in self._groups.items():
            if state.process and state.container_name:
                try:
                    if state.process.returncode is None:
                        active_containers.append(state.container_name)
                except Exception:
                    pass

        logger.info(
            "GroupQueue shutting down (containers detached, not killed)",
            active_count=self._active_count,
            detached_containers=active_containers,
        )
