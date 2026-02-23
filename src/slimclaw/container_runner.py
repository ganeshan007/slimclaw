"""Container Runner for SlimClaw
Spawns agent execution in containers and handles IPC.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from slimclaw.config import (
    CONTAINER_IMAGE,
    CONTAINER_MAX_OUTPUT_SIZE,
    CONTAINER_TIMEOUT,
    DATA_DIR,
    GROUPS_DIR,
    IDLE_TIMEOUT,
    PROJECT_ROOT,
)
from slimclaw.container_runtime import CONTAINER_RUNTIME_BIN, readonly_mount_args, stop_container
from slimclaw.env import read_env_file
from slimclaw.logger import logger
from slimclaw.mount_security import validate_additional_mounts
from slimclaw.types import RegisteredGroup

OUTPUT_START_MARKER = "---NANOCLAW_OUTPUT_START---"
OUTPUT_END_MARKER = "---NANOCLAW_OUTPUT_END---"


def _get_home_dir() -> str:
    home = os.environ.get("HOME") or str(Path.home())
    if not home:
        raise RuntimeError(
            "Unable to determine home directory: HOME environment variable is not set"
        )
    return home


@dataclass
class ContainerInput:
    prompt: str
    group_folder: str
    chat_jid: str
    is_main: bool
    session_id: Optional[str] = None
    is_scheduled_task: Optional[bool] = None
    secrets: Optional[dict[str, str]] = None

    def to_json_dict(self) -> dict:
        """Serialize to camelCase JSON dict matching container protocol."""
        d: dict = {
            "prompt": self.prompt,
            "groupFolder": self.group_folder,
            "chatJid": self.chat_jid,
            "isMain": self.is_main,
        }
        if self.session_id is not None:
            d["sessionId"] = self.session_id
        if self.is_scheduled_task is not None:
            d["isScheduledTask"] = self.is_scheduled_task
        if self.secrets is not None:
            d["secrets"] = self.secrets
        return d


@dataclass
class ContainerOutput:
    status: str  # 'success' | 'error'
    result: Optional[str]
    new_session_id: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def from_json(cls, data: dict) -> ContainerOutput:
        return cls(
            status=data.get("status", "error"),
            result=data.get("result"),
            new_session_id=data.get("newSessionId"),
            error=data.get("error"),
        )


@dataclass
class VolumeMount:
    host_path: str
    container_path: str
    readonly: bool


def _build_volume_mounts(group: RegisteredGroup, is_main: bool) -> list[VolumeMount]:
    mounts: list[VolumeMount] = []
    project_root = str(PROJECT_ROOT)

    if is_main:
        mounts.append(VolumeMount(
            host_path=project_root,
            container_path="/workspace/project",
            readonly=False,
        ))
        mounts.append(VolumeMount(
            host_path=str(GROUPS_DIR / group.folder),
            container_path="/workspace/group",
            readonly=False,
        ))
    else:
        mounts.append(VolumeMount(
            host_path=str(GROUPS_DIR / group.folder),
            container_path="/workspace/group",
            readonly=False,
        ))
        global_dir = GROUPS_DIR / "global"
        if global_dir.exists():
            mounts.append(VolumeMount(
                host_path=str(global_dir),
                container_path="/workspace/global",
                readonly=True,
            ))

    # Per-group Claude sessions directory
    group_sessions_dir = DATA_DIR / "sessions" / group.folder / ".claude"
    group_sessions_dir.mkdir(parents=True, exist_ok=True)
    settings_file = group_sessions_dir / "settings.json"
    if not settings_file.exists():
        settings_file.write_text(
            json.dumps(
                {
                    "env": {
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                        "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD": "1",
                        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
                    }
                },
                indent=2,
            )
            + "\n"
        )

    # Sync skills from container/skills/ into each group's .claude/skills/
    skills_src = PROJECT_ROOT / "container" / "skills"
    skills_dst = group_sessions_dir / "skills"
    if skills_src.exists():
        for skill_dir in skills_src.iterdir():
            if not skill_dir.is_dir():
                continue
            dst_dir = skills_dst / skill_dir.name
            shutil.copytree(str(skill_dir), str(dst_dir), dirs_exist_ok=True)

    mounts.append(VolumeMount(
        host_path=str(group_sessions_dir),
        container_path="/home/node/.claude",
        readonly=False,
    ))

    # Per-group IPC namespace
    group_ipc_dir = DATA_DIR / "ipc" / group.folder
    (group_ipc_dir / "messages").mkdir(parents=True, exist_ok=True)
    (group_ipc_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (group_ipc_dir / "input").mkdir(parents=True, exist_ok=True)
    mounts.append(VolumeMount(
        host_path=str(group_ipc_dir),
        container_path="/workspace/ipc",
        readonly=False,
    ))

    # Mount agent-runner source from host
    agent_runner_src = PROJECT_ROOT / "container" / "agent-runner" / "src"
    mounts.append(VolumeMount(
        host_path=str(agent_runner_src),
        container_path="/app/src",
        readonly=True,
    ))

    # Additional mounts validated against external allowlist
    if group.container_config and group.container_config.additional_mounts:
        validated = validate_additional_mounts(
            group.container_config.additional_mounts,
            group.name,
            is_main,
        )
        for vm in validated:
            mounts.append(VolumeMount(
                host_path=vm.host_path,
                container_path=vm.container_path,
                readonly=vm.readonly,
            ))

    return mounts


def _read_secrets() -> dict[str, str]:
    return read_env_file(["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_MODEL"])


def _build_container_args(mounts: list[VolumeMount], container_name: str) -> list[str]:
    args = ["run", "-i", "--rm", "--name", container_name]

    host_uid = os.getuid()
    host_gid = os.getgid()
    if host_uid != 0 and host_uid != 1000:
        args.extend(["--user", f"{host_uid}:{host_gid}"])
        args.extend(["-e", "HOME=/home/node"])

    for mount in mounts:
        if mount.readonly:
            args.extend(readonly_mount_args(mount.host_path, mount.container_path))
        else:
            args.extend(["-v", f"{mount.host_path}:{mount.container_path}"])

    args.append(CONTAINER_IMAGE)
    return args


async def run_container_agent(
    group: RegisteredGroup,
    input_data: ContainerInput,
    on_process: Callable[[asyncio.subprocess.Process, str], None],
    on_output: Optional[Callable[[ContainerOutput], Awaitable[None]]] = None,
) -> ContainerOutput:
    start_time = time.monotonic()

    group_dir = GROUPS_DIR / group.folder
    group_dir.mkdir(parents=True, exist_ok=True)

    mounts = _build_volume_mounts(group, input_data.is_main)
    safe_name = "".join(c if c.isalnum() or c == "-" else "-" for c in group.folder)
    container_name = f"slimclaw-{safe_name}-{int(time.time() * 1000)}"
    container_args = _build_container_args(mounts, container_name)

    logger.debug(
        "Container mount configuration",
        group=group.name,
        container_name=container_name,
        mounts=[
            f"{m.host_path} -> {m.container_path}{' (ro)' if m.readonly else ''}"
            for m in mounts
        ],
    )

    logger.info(
        "Spawning container agent",
        group=group.name,
        container_name=container_name,
        mount_count=len(mounts),
        is_main=input_data.is_main,
    )

    logs_dir = GROUPS_DIR / group.folder / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Spawn the container
    proc = await asyncio.create_subprocess_exec(
        CONTAINER_RUNTIME_BIN,
        *container_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    on_process(proc, container_name)

    # Pass secrets via stdin
    input_data.secrets = _read_secrets()
    stdin_data = json.dumps(input_data.to_json_dict()).encode()
    input_data.secrets = None  # Remove from memory

    assert proc.stdin is not None
    proc.stdin.write(stdin_data)
    proc.stdin.close()

    stdout_buf = bytearray()
    stderr_buf = bytearray()
    stdout_truncated = False
    stderr_truncated = False

    # Streaming output state
    parse_buffer = ""
    new_session_id: Optional[str] = None
    had_streaming_output = False
    output_tasks: list[asyncio.Task] = []

    # Timeout management
    timed_out = False
    config_timeout = (group.container_config.timeout if group.container_config and group.container_config.timeout else CONTAINER_TIMEOUT)
    timeout_ms = max(config_timeout, IDLE_TIMEOUT + 30_000)
    timeout_handle: Optional[asyncio.TimerHandle] = None

    async def kill_on_timeout():
        nonlocal timed_out
        timed_out = True
        logger.error("Container timeout, stopping gracefully", group=group.name, container_name=container_name)
        try:
            stop_proc = await asyncio.create_subprocess_exec(
                *stop_container(container_name).split(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(stop_proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                logger.warning("Graceful stop failed, force killing", group=group.name)
                proc.kill()
        except Exception:
            proc.kill()

    timeout_task: Optional[asyncio.Task] = None

    def reset_timeout():
        nonlocal timeout_task
        if timeout_task and not timeout_task.done():
            timeout_task.cancel()

        async def _delayed_kill():
            await asyncio.sleep(timeout_ms / 1000)
            await kill_on_timeout()

        timeout_task = asyncio.create_task(_delayed_kill())

    reset_timeout()

    # Read stdout
    async def read_stdout():
        nonlocal parse_buffer, new_session_id, had_streaming_output, stdout_truncated
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            chunk_str = chunk.decode("utf-8", errors="replace")

            # Accumulate for logging
            if not stdout_truncated:
                remaining = CONTAINER_MAX_OUTPUT_SIZE - len(stdout_buf)
                if len(chunk) > remaining:
                    stdout_buf.extend(chunk[:remaining])
                    stdout_truncated = True
                    logger.warning("Container stdout truncated", group=group.name, size=len(stdout_buf))
                else:
                    stdout_buf.extend(chunk)

            # Stream-parse for output markers
            if on_output:
                parse_buffer += chunk_str
                while True:
                    start_idx = parse_buffer.find(OUTPUT_START_MARKER)
                    if start_idx == -1:
                        break
                    end_idx = parse_buffer.find(OUTPUT_END_MARKER, start_idx)
                    if end_idx == -1:
                        break

                    json_str = parse_buffer[
                        start_idx + len(OUTPUT_START_MARKER) : end_idx
                    ].strip()
                    parse_buffer = parse_buffer[end_idx + len(OUTPUT_END_MARKER) :]

                    try:
                        parsed = ContainerOutput.from_json(json.loads(json_str))
                        if parsed.new_session_id:
                            new_session_id = parsed.new_session_id
                        had_streaming_output = True
                        reset_timeout()
                        task = asyncio.create_task(on_output(parsed))
                        output_tasks.append(task)
                    except Exception as err:
                        logger.warning(
                            "Failed to parse streamed output chunk",
                            group=group.name,
                            error=str(err),
                        )

    # Read stderr
    async def read_stderr():
        nonlocal stderr_truncated
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            chunk_str = chunk.decode("utf-8", errors="replace")
            for line in chunk_str.strip().split("\n"):
                if line:
                    logger.debug(line, container=group.folder)
            if stderr_truncated:
                continue
            remaining = CONTAINER_MAX_OUTPUT_SIZE - len(stderr_buf)
            if len(chunk) > remaining:
                stderr_buf.extend(chunk[:remaining])
                stderr_truncated = True
                logger.warning("Container stderr truncated", group=group.name, size=len(stderr_buf))
            else:
                stderr_buf.extend(chunk)

    # Run stdout/stderr readers concurrently and wait for process
    await asyncio.gather(read_stdout(), read_stderr())
    code = await proc.wait()

    # Cancel timeout
    if timeout_task and not timeout_task.done():
        timeout_task.cancel()

    # Wait for all output callbacks to complete
    if output_tasks:
        await asyncio.gather(*output_tasks, return_exceptions=True)

    duration = int((time.monotonic() - start_time) * 1000)
    stdout_str = stdout_buf.decode("utf-8", errors="replace")
    stderr_str = stderr_buf.decode("utf-8", errors="replace")

    if timed_out:
        ts = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")
        timeout_log = logs_dir / f"container-{ts}.log"
        timeout_log.write_text(
            f"=== Container Run Log (TIMEOUT) ===\n"
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"Group: {group.name}\n"
            f"Container: {container_name}\n"
            f"Duration: {duration}ms\n"
            f"Exit Code: {code}\n"
            f"Had Streaming Output: {had_streaming_output}\n"
        )

        if had_streaming_output:
            logger.info(
                "Container timed out after output (idle cleanup)",
                group=group.name,
                container_name=container_name,
                duration=duration,
                code=code,
            )
            return ContainerOutput(status="success", result=None, new_session_id=new_session_id)

        logger.error(
            "Container timed out with no output",
            group=group.name,
            container_name=container_name,
            duration=duration,
            code=code,
        )
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Container timed out after {config_timeout}ms",
        )

    # Write log file
    ts = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")
    log_file = logs_dir / f"container-{ts}.log"
    is_verbose = os.environ.get("LOG_LEVEL") in ("debug", "trace")
    is_error = code != 0

    log_lines = [
        "=== Container Run Log ===",
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"Group: {group.name}",
        f"IsMain: {input_data.is_main}",
        f"Duration: {duration}ms",
        f"Exit Code: {code}",
        f"Stdout Truncated: {stdout_truncated}",
        f"Stderr Truncated: {stderr_truncated}",
        "",
    ]

    if is_verbose or is_error:
        input_log = ContainerInput(
            prompt=input_data.prompt,
            group_folder=input_data.group_folder,
            chat_jid=input_data.chat_jid,
            is_main=input_data.is_main,
            session_id=input_data.session_id,
            is_scheduled_task=input_data.is_scheduled_task,
        )
        log_lines.extend([
            "=== Input ===",
            json.dumps(input_log.to_json_dict(), indent=2),
            "",
            "=== Container Args ===",
            " ".join(container_args),
            "",
            "=== Mounts ===",
            "\n".join(
                f"{m.host_path} -> {m.container_path}{' (ro)' if m.readonly else ''}"
                for m in mounts
            ),
            "",
            f"=== Stderr{' (TRUNCATED)' if stderr_truncated else ''} ===",
            stderr_str,
            "",
            f"=== Stdout{' (TRUNCATED)' if stdout_truncated else ''} ===",
            stdout_str,
        ])
    else:
        log_lines.extend([
            "=== Input Summary ===",
            f"Prompt length: {len(input_data.prompt)} chars",
            f"Session ID: {input_data.session_id or 'new'}",
            "",
            "=== Mounts ===",
            "\n".join(
                f"{m.container_path}{' (ro)' if m.readonly else ''}" for m in mounts
            ),
            "",
        ])

    log_file.write_text("\n".join(log_lines))
    logger.debug("Container log written", log_file=str(log_file), verbose=is_verbose)

    if code != 0:
        logger.error(
            "Container exited with error",
            group=group.name,
            code=code,
            duration=duration,
            log_file=str(log_file),
        )
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Container exited with code {code}: {stderr_str[-200:]}",
        )

    # Streaming mode
    if on_output:
        logger.info(
            "Container completed (streaming mode)",
            group=group.name,
            duration=duration,
            new_session_id=new_session_id,
        )
        return ContainerOutput(status="success", result=None, new_session_id=new_session_id)

    # Legacy mode: parse the last output marker pair
    try:
        start_idx = stdout_str.find(OUTPUT_START_MARKER)
        end_idx = stdout_str.find(OUTPUT_END_MARKER)

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_line = stdout_str[start_idx + len(OUTPUT_START_MARKER) : end_idx].strip()
        else:
            lines = stdout_str.strip().split("\n")
            json_line = lines[-1]

        output = ContainerOutput.from_json(json.loads(json_line))

        logger.info(
            "Container completed",
            group=group.name,
            duration=duration,
            status=output.status,
            has_result=bool(output.result),
        )
        return output

    except Exception as err:
        logger.error(
            "Failed to parse container output",
            group=group.name,
            error=str(err),
        )
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Failed to parse container output: {err}",
        )


def write_tasks_snapshot(
    group_folder: str,
    is_main: bool,
    tasks: list[dict],
) -> None:
    group_ipc_dir = DATA_DIR / "ipc" / group_folder
    group_ipc_dir.mkdir(parents=True, exist_ok=True)

    filtered_tasks = tasks if is_main else [t for t in tasks if t.get("groupFolder") == group_folder]

    tasks_file = group_ipc_dir / "current_tasks.json"
    tasks_file.write_text(json.dumps(filtered_tasks, indent=2))


@dataclass
class AvailableGroup:
    jid: str
    name: str
    last_activity: str
    is_registered: bool


def write_groups_snapshot(
    group_folder: str,
    is_main: bool,
    groups: list[AvailableGroup],
    registered_jids: set[str],
) -> None:
    group_ipc_dir = DATA_DIR / "ipc" / group_folder
    group_ipc_dir.mkdir(parents=True, exist_ok=True)

    visible_groups = (
        [
            {
                "jid": g.jid,
                "name": g.name,
                "lastActivity": g.last_activity,
                "isRegistered": g.is_registered,
            }
            for g in groups
        ]
        if is_main
        else []
    )

    groups_file = group_ipc_dir / "available_groups.json"
    groups_file.write_text(
        json.dumps(
            {
                "groups": visible_groups,
                "lastSync": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
