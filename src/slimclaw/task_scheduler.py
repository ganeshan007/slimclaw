from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

from croniter import croniter

from slimclaw.config import (
    GROUPS_DIR,
    IDLE_TIMEOUT,
    MAIN_GROUP_FOLDER,
    SCHEDULER_POLL_INTERVAL,
    TIMEZONE,
)
from slimclaw.container_runner import (
    ContainerInput,
    ContainerOutput,
    run_container_agent,
    write_tasks_snapshot,
)
from slimclaw.db import (
    get_all_tasks,
    get_due_tasks,
    get_task_by_id,
    log_task_run,
    update_task_after_run,
)
from slimclaw.group_queue import GroupQueue
from slimclaw.logger import logger
from slimclaw.types import RegisteredGroup, ScheduledTask, TaskRunLog


class SchedulerDependencies(Protocol):
    def registered_groups(self) -> dict[str, RegisteredGroup]: ...
    def get_sessions(self) -> dict[str, str]: ...
    @property
    def queue(self) -> GroupQueue: ...
    def on_process(
        self,
        group_jid: str,
        proc: asyncio.subprocess.Process,
        container_name: str,
        group_folder: str,
    ) -> None: ...
    async def send_message(self, jid: str, text: str) -> None: ...


async def _run_task_tracked(task: ScheduledTask, deps: SchedulerDependencies) -> None:
    """Wrapper that clears the inflight set after task execution."""
    try:
        await _run_task(task, deps)
    finally:
        _inflight_tasks.discard(task.id)


async def _run_task(task: ScheduledTask, deps: SchedulerDependencies) -> None:
    start_time = time.monotonic()
    group_dir = GROUPS_DIR / task.group_folder
    group_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Running scheduled task", task_id=task.id, group=task.group_folder)

    groups = deps.registered_groups()
    group = next((g for g in groups.values() if g.folder == task.group_folder), None)

    if not group:
        logger.error(
            "Group not found for task",
            task_id=task.id,
            group_folder=task.group_folder,
        )
        log_task_run(
            TaskRunLog(
                task_id=task.id,
                run_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=int((time.monotonic() - start_time) * 1000),
                status="error",
                result=None,
                error=f"Group not found: {task.group_folder}",
            )
        )
        return

    is_main = task.group_folder == MAIN_GROUP_FOLDER
    all_tasks = get_all_tasks()
    write_tasks_snapshot(
        task.group_folder,
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
            for t in all_tasks
        ],
    )

    result: Optional[str] = None
    error: Optional[str] = None

    sessions = deps.get_sessions()
    session_id = sessions.get(task.group_folder) if task.context_mode == "group" else None

    # Idle timer
    idle_task: Optional[asyncio.Task] = None

    def reset_idle_timer():
        nonlocal idle_task
        if idle_task and not idle_task.done():
            idle_task.cancel()

        async def _idle_close():
            await asyncio.sleep(IDLE_TIMEOUT / 1000)
            logger.debug("Scheduled task idle timeout, closing container stdin", task_id=task.id)
            deps.queue.close_stdin(task.chat_jid)

        idle_task = asyncio.create_task(_idle_close())

    try:
        output = await run_container_agent(
            group,
            ContainerInput(
                prompt=task.prompt,
                session_id=session_id,
                group_folder=task.group_folder,
                chat_jid=task.chat_jid,
                is_main=is_main,
                is_scheduled_task=True,
            ),
            lambda proc, cn: deps.on_process(task.chat_jid, proc, cn, task.group_folder),
            on_output=_make_on_output(task, deps, lambda: reset_idle_timer(), result_holder := {"result": None, "error": None}),
        )

        if idle_task and not idle_task.done():
            idle_task.cancel()

        result = result_holder["result"]
        error = result_holder["error"]

        if output.status == "error":
            error = output.error or "Unknown error"
        elif output.result:
            result = output.result

        logger.info(
            "Task completed",
            task_id=task.id,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
    except Exception as err:
        if idle_task and not idle_task.done():
            idle_task.cancel()
        error = str(err)
        logger.error("Task failed", task_id=task.id, error=error)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    log_task_run(
        TaskRunLog(
            task_id=task.id,
            run_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            status="error" if error else "success",
            result=result,
            error=error,
        )
    )

    next_run: Optional[str] = None
    if task.schedule_type == "cron":
        cron = croniter(task.schedule_value)
        next_run = datetime.fromtimestamp(cron.get_next(float), tz=timezone.utc).isoformat()
    elif task.schedule_type == "interval":
        ms = int(task.schedule_value)
        next_run = datetime.fromtimestamp(time.time() + ms / 1000, tz=timezone.utc).isoformat()
    # 'once' tasks have no next run

    result_summary = (
        f"Error: {error}"
        if error
        else (result[:200] if result else "Completed")
    )
    update_task_after_run(task.id, next_run, result_summary)


def _make_on_output(
    task: ScheduledTask,
    deps: SchedulerDependencies,
    reset_idle: Callable[[], None],
    holder: dict,
) -> Callable[[ContainerOutput], Awaitable[None]]:
    async def on_output(streamed: ContainerOutput) -> None:
        if streamed.result:
            holder["result"] = streamed.result
            await deps.send_message(task.chat_jid, streamed.result)
            reset_idle()
        if streamed.status == "error":
            holder["error"] = streamed.error or "Unknown error"

    return on_output


_scheduler_running = False
_inflight_tasks: set[str] = set()  # task IDs currently being executed


async def start_scheduler_loop(deps: SchedulerDependencies) -> None:
    global _scheduler_running
    if _scheduler_running:
        logger.debug("Scheduler loop already running, skipping duplicate start")
        return
    _scheduler_running = True
    logger.info("Scheduler loop started")

    while True:
        try:
            due_tasks = get_due_tasks()
            # Filter out tasks already in-flight (prevents re-enqueue on next poll)
            new_due = [t for t in due_tasks if t.id not in _inflight_tasks]
            if new_due:
                logger.info("Found due tasks", count=len(new_due))

            for task in new_due:
                current = get_task_by_id(task.id)
                if not current or current.status != "active":
                    continue

                _inflight_tasks.add(current.id)
                deps.queue.enqueue_task(
                    current.chat_jid,
                    current.id,
                    lambda t=current: _run_task_tracked(t, deps),
                )
        except Exception as err:
            logger.error("Error in scheduler loop", error=str(err))

        await asyncio.sleep(SCHEDULER_POLL_INTERVAL)
