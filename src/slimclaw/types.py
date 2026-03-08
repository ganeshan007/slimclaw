from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol


@dataclass(slots=True)
class AdditionalMount:
    host_path: str
    container_path: Optional[str] = None
    readonly: Optional[bool] = None


@dataclass(slots=True)
class AllowedRoot:
    path: str
    allow_read_write: bool
    description: Optional[str] = None


@dataclass
class MountAllowlist:
    allowed_roots: list[AllowedRoot] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)
    non_main_read_only: bool = True


@dataclass(slots=True)
class ContainerConfig:
    additional_mounts: Optional[list[AdditionalMount]] = None
    timeout: Optional[int] = None


@dataclass(slots=True)
class RegisteredGroup:
    name: str
    folder: str
    trigger: str
    added_at: str
    container_config: Optional[ContainerConfig] = None
    requires_trigger: Optional[bool] = None


@dataclass(slots=True)
class NewMessage:
    id: str
    chat_jid: str
    sender: str
    sender_name: str
    content: str
    timestamp: str
    is_from_me: Optional[bool] = None
    is_bot_message: Optional[bool] = None


@dataclass(slots=True)
class ScheduledTask:
    id: str
    group_folder: str
    chat_jid: str
    prompt: str
    schedule_type: str  # 'cron' | 'interval' | 'once'
    schedule_value: str
    context_mode: str  # 'group' | 'isolated'
    next_run: Optional[str]
    last_run: Optional[str]
    last_result: Optional[str]
    status: str  # 'active' | 'paused' | 'completed'
    created_at: str


@dataclass(slots=True)
class TaskRunLog:
    task_id: str
    run_at: str
    duration_ms: int
    status: str  # 'success' | 'error'
    result: Optional[str]
    error: Optional[str]


class Channel(Protocol):
    name: str

    async def connect(self) -> None: ...
    async def send_message(self, jid: str, text: str) -> None: ...
    def is_connected(self) -> bool: ...
    def owns_jid(self, jid: str) -> bool: ...
    async def disconnect(self) -> None: ...
    async def set_typing(self, jid: str, is_typing: bool) -> None: ...


# Callback types
OnInboundMessage = Callable[[str, NewMessage], None]
OnChatMetadata = Callable[[str, str, Optional[str], Optional[str], Optional[bool]], None]
OnUnregisteredTrigger = Callable[[str, str, str], None]  # (chat_jid, sender_name, content)


@dataclass
class AppOpts:
    """Generic constructor options accepted by all app channels."""
    on_message: OnInboundMessage
    on_chat_metadata: OnChatMetadata
    registered_groups: Callable[[], dict[str, RegisteredGroup]]
    on_unregistered_trigger: OnUnregisteredTrigger | None = None
