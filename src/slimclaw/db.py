from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from slimclaw.config import ASSISTANT_NAME, DATA_DIR, STORE_DIR
from slimclaw.types import NewMessage, RegisteredGroup, ScheduledTask, TaskRunLog, ContainerConfig, AdditionalMount

_db: Optional[sqlite3.Connection] = None


def _get_db() -> sqlite3.Connection:
    assert _db is not None, "Database not initialized. Call init_database() first."
    return _db


def _create_schema(database: sqlite3.Connection) -> None:
    database.execute("BEGIN")
    database.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
            jid TEXT PRIMARY KEY,
            name TEXT,
            last_message_time TEXT,
            channel TEXT,
            is_group INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT,
            chat_jid TEXT,
            sender TEXT,
            sender_name TEXT,
            content TEXT,
            timestamp TEXT,
            is_from_me INTEGER,
            is_bot_message INTEGER DEFAULT 0,
            PRIMARY KEY (id, chat_jid),
            FOREIGN KEY (chat_jid) REFERENCES chats(jid)
        );
        CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp);

        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            group_folder TEXT NOT NULL,
            chat_jid TEXT NOT NULL,
            prompt TEXT NOT NULL,
            schedule_type TEXT NOT NULL,
            schedule_value TEXT NOT NULL,
            next_run TEXT,
            last_run TEXT,
            last_result TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_next_run ON scheduled_tasks(next_run);
        CREATE INDEX IF NOT EXISTS idx_status ON scheduled_tasks(status);

        CREATE TABLE IF NOT EXISTS task_run_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            run_at TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            error TEXT,
            FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_task_run_logs ON task_run_logs(task_id, run_at);

        CREATE TABLE IF NOT EXISTS router_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            group_folder TEXT PRIMARY KEY,
            session_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registered_groups (
            jid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folder TEXT NOT NULL UNIQUE,
            trigger_pattern TEXT NOT NULL,
            added_at TEXT NOT NULL,
            container_config TEXT,
            requires_trigger INTEGER DEFAULT 1
        );
    """)

    # Add context_mode column if it doesn't exist (migration for existing DBs)
    try:
        database.execute(
            "ALTER TABLE scheduled_tasks ADD COLUMN context_mode TEXT DEFAULT 'isolated'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Add is_bot_message column if it doesn't exist (migration for existing DBs)
    try:
        database.execute(
            "ALTER TABLE messages ADD COLUMN is_bot_message INTEGER DEFAULT 0"
        )
        # Backfill: mark existing bot messages that used the content prefix pattern
        database.execute(
            "UPDATE messages SET is_bot_message = 1 WHERE content LIKE ?",
            (f"{ASSISTANT_NAME}:%",),
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Add channel and is_group columns if they don't exist (migration for existing DBs)
    try:
        database.execute("ALTER TABLE chats ADD COLUMN channel TEXT")
        database.execute("ALTER TABLE chats ADD COLUMN is_group INTEGER DEFAULT 0")
        # Backfill from JID patterns
        database.execute(
            "UPDATE chats SET channel = 'whatsapp', is_group = 1 WHERE jid LIKE '%@g.us'"
        )
        database.execute(
            "UPDATE chats SET channel = 'whatsapp', is_group = 0 WHERE jid LIKE '%@s.whatsapp.net'"
        )
        database.execute(
            "UPDATE chats SET channel = 'discord', is_group = 1 WHERE jid LIKE 'dc:%'"
        )
        database.execute(
            "UPDATE chats SET channel = 'telegram', is_group = 1 WHERE jid LIKE 'tg:%'"
        )
    except sqlite3.OperationalError:
        pass  # columns already exist




def init_database() -> None:
    global _db
    db_path = STORE_DIR / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _db = sqlite3.connect(str(db_path), isolation_level=None)  # autocommit mode
    _db.row_factory = sqlite3.Row
    _db.execute("PRAGMA journal_mode=WAL")
    _db.execute("PRAGMA synchronous=NORMAL")
    _create_schema(_db)

    # Migrate from JSON files if they exist
    _migrate_json_state()


def _init_test_database() -> None:
    """For tests only. Creates a fresh in-memory database."""
    global _db
    _db = sqlite3.connect(":memory:", isolation_level=None)  # autocommit mode
    _db.row_factory = sqlite3.Row
    _create_schema(_db)


# --- Chat metadata ---


def store_chat_metadata(
    chat_jid: str,
    timestamp: str,
    name: Optional[str] = None,
    channel: Optional[str] = None,
    is_group: Optional[bool] = None,
) -> None:
    db = _get_db()
    ch = channel
    group = None if is_group is None else (1 if is_group else 0)

    if name:
        db.execute(
            """
            INSERT INTO chats (jid, name, last_message_time, channel, is_group) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                name = excluded.name,
                last_message_time = MAX(last_message_time, excluded.last_message_time),
                channel = COALESCE(excluded.channel, channel),
                is_group = COALESCE(excluded.is_group, is_group)
            """,
            (chat_jid, name, timestamp, ch, group),
        )
    else:
        db.execute(
            """
            INSERT INTO chats (jid, name, last_message_time, channel, is_group) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                last_message_time = MAX(last_message_time, excluded.last_message_time),
                channel = COALESCE(excluded.channel, channel),
                is_group = COALESCE(excluded.is_group, is_group)
            """,
            (chat_jid, chat_jid, timestamp, ch, group),
        )



def update_chat_name(chat_jid: str, name: str) -> None:
    db = _get_db()
    db.execute(
        """
        INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
        ON CONFLICT(jid) DO UPDATE SET name = excluded.name
        """,
        (chat_jid, name, datetime.now(timezone.utc).isoformat()),
    )



@dataclass
class ChatInfo:
    jid: str
    name: str
    last_message_time: str
    channel: Optional[str]
    is_group: int


def get_all_chats() -> list[ChatInfo]:
    db = _get_db()
    rows = db.execute(
        "SELECT jid, name, last_message_time, channel, is_group FROM chats ORDER BY last_message_time DESC"
    ).fetchall()
    return [ChatInfo(**dict(row)) for row in rows]


def get_last_group_sync() -> Optional[str]:
    db = _get_db()
    row = db.execute(
        "SELECT last_message_time FROM chats WHERE jid = '__group_sync__'"
    ).fetchone()
    return row["last_message_time"] if row else None


def set_last_group_sync() -> None:
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO chats (jid, name, last_message_time) VALUES ('__group_sync__', '__group_sync__', ?)",
        (now,),
    )



# --- Messages ---


def store_message(msg: NewMessage) -> None:
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO messages (id, chat_jid, sender, sender_name, content, timestamp, is_from_me, is_bot_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            msg.id,
            msg.chat_jid,
            msg.sender,
            msg.sender_name,
            msg.content,
            msg.timestamp,
            1 if msg.is_from_me else 0,
            1 if msg.is_bot_message else 0,
        ),
    )



def store_message_direct(
    *,
    id: str,
    chat_jid: str,
    sender: str,
    sender_name: str,
    content: str,
    timestamp: str,
    is_from_me: bool,
    is_bot_message: bool = False,
) -> None:
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO messages (id, chat_jid, sender, sender_name, content, timestamp, is_from_me, is_bot_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (id, chat_jid, sender, sender_name, content, timestamp, 1 if is_from_me else 0, 1 if is_bot_message else 0),
    )



def get_new_messages(
    jids: list[str],
    last_timestamp: str,
    bot_prefix: str,
) -> tuple[list[NewMessage], str]:
    if not jids:
        return [], last_timestamp

    db = _get_db()
    placeholders = ",".join("?" for _ in jids)
    sql = f"""
        SELECT id, chat_jid, sender, sender_name, content, timestamp
        FROM messages
        WHERE timestamp > ? AND chat_jid IN ({placeholders})
            AND is_bot_message = 0 AND content NOT LIKE ?
        ORDER BY timestamp
    """
    params = [last_timestamp, *jids, f"{bot_prefix}:%"]
    rows = db.execute(sql, params).fetchall()

    messages = [
        NewMessage(
            id=row["id"],
            chat_jid=row["chat_jid"],
            sender=row["sender"],
            sender_name=row["sender_name"],
            content=row["content"],
            timestamp=row["timestamp"],
        )
        for row in rows
    ]

    new_timestamp = last_timestamp
    for msg in messages:
        if msg.timestamp > new_timestamp:
            new_timestamp = msg.timestamp

    return messages, new_timestamp


def get_messages_since(
    chat_jid: str,
    since_timestamp: str,
    bot_prefix: str,
) -> list[NewMessage]:
    db = _get_db()
    sql = """
        SELECT id, chat_jid, sender, sender_name, content, timestamp
        FROM messages
        WHERE chat_jid = ? AND timestamp > ?
            AND is_bot_message = 0 AND content NOT LIKE ?
        ORDER BY timestamp
    """
    rows = db.execute(sql, (chat_jid, since_timestamp, f"{bot_prefix}:%")).fetchall()
    return [
        NewMessage(
            id=row["id"],
            chat_jid=row["chat_jid"],
            sender=row["sender"],
            sender_name=row["sender_name"],
            content=row["content"],
            timestamp=row["timestamp"],
        )
        for row in rows
    ]


# --- Scheduled tasks ---


def create_task(task: ScheduledTask) -> None:
    db = _get_db()
    db.execute(
        """
        INSERT INTO scheduled_tasks (id, group_folder, chat_jid, prompt, schedule_type, schedule_value, context_mode, next_run, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.id,
            task.group_folder,
            task.chat_jid,
            task.prompt,
            task.schedule_type,
            task.schedule_value,
            task.context_mode or "isolated",
            task.next_run,
            task.status,
            task.created_at,
        ),
    )



def get_task_by_id(task_id: str) -> Optional[ScheduledTask]:
    db = _get_db()
    row = db.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    return _row_to_task(row)


def get_tasks_for_group(group_folder: str) -> list[ScheduledTask]:
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM scheduled_tasks WHERE group_folder = ? ORDER BY created_at DESC",
        (group_folder,),
    ).fetchall()
    return [_row_to_task(row) for row in rows]


def get_all_tasks() -> list[ScheduledTask]:
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_task(row) for row in rows]


def update_task(task_id: str, **updates: str | None) -> None:
    allowed = {"prompt", "schedule_type", "schedule_value", "next_run", "status"}
    fields: list[str] = []
    values: list[str | None] = []

    for key, val in updates.items():
        if key in allowed and val is not None:
            fields.append(f"{key} = ?")
            values.append(val)

    if not fields:
        return

    db = _get_db()
    values.append(task_id)
    db.execute(
        f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id = ?",
        values,
    )



def delete_task(task_id: str) -> None:
    db = _get_db()
    db.execute("DELETE FROM task_run_logs WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))



def get_due_tasks() -> list[ScheduledTask]:
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        """
        SELECT * FROM scheduled_tasks
        WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ?
        ORDER BY next_run
        """,
        (now,),
    ).fetchall()
    return [_row_to_task(row) for row in rows]


def update_task_after_run(
    task_id: str,
    next_run: Optional[str],
    last_result: str,
) -> None:
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        UPDATE scheduled_tasks
        SET next_run = ?, last_run = ?, last_result = ?, status = CASE WHEN ? IS NULL THEN 'completed' ELSE status END
        WHERE id = ?
        """,
        (next_run, now, last_result, next_run, task_id),
    )



def log_task_run(log: TaskRunLog) -> None:
    db = _get_db()
    db.execute(
        """
        INSERT INTO task_run_logs (task_id, run_at, duration_ms, status, result, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (log.task_id, log.run_at, log.duration_ms, log.status, log.result, log.error),
    )



# --- Router state ---


def get_router_state(key: str) -> Optional[str]:
    db = _get_db()
    row = db.execute("SELECT value FROM router_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_router_state(key: str, value: str) -> None:
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO router_state (key, value) VALUES (?, ?)",
        (key, value),
    )



# --- Sessions ---


def get_session(group_folder: str) -> Optional[str]:
    db = _get_db()
    row = db.execute(
        "SELECT session_id FROM sessions WHERE group_folder = ?", (group_folder,)
    ).fetchone()
    return row["session_id"] if row else None


def set_session(group_folder: str, session_id: str) -> None:
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO sessions (group_folder, session_id) VALUES (?, ?)",
        (group_folder, session_id),
    )



def get_all_sessions() -> dict[str, str]:
    db = _get_db()
    rows = db.execute("SELECT group_folder, session_id FROM sessions").fetchall()
    return {row["group_folder"]: row["session_id"] for row in rows}


# --- Registered groups ---


def get_registered_group(jid: str) -> Optional[RegisteredGroup]:
    db = _get_db()
    row = db.execute("SELECT * FROM registered_groups WHERE jid = ?", (jid,)).fetchone()
    if not row:
        return None
    return _row_to_registered_group(row)


def set_registered_group(jid: str, group: RegisteredGroup) -> None:
    db = _get_db()
    db.execute(
        """INSERT OR REPLACE INTO registered_groups (jid, name, folder, trigger_pattern, added_at, container_config, requires_trigger)
         VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            jid,
            group.name,
            group.folder,
            group.trigger,
            group.added_at,
            json.dumps(_container_config_to_dict(group.container_config)) if group.container_config else None,
            1 if group.requires_trigger is None else (1 if group.requires_trigger else 0),
        ),
    )



def get_all_registered_groups() -> dict[str, RegisteredGroup]:
    db = _get_db()
    rows = db.execute("SELECT * FROM registered_groups").fetchall()
    result: dict[str, RegisteredGroup] = {}
    for row in rows:
        result[row["jid"]] = _row_to_registered_group(row)
    return result


# --- Helpers ---


def _row_to_task(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        group_folder=row["group_folder"],
        chat_jid=row["chat_jid"],
        prompt=row["prompt"],
        schedule_type=row["schedule_type"],
        schedule_value=row["schedule_value"],
        context_mode=row["context_mode"] or "isolated",
        next_run=row["next_run"],
        last_run=row["last_run"],
        last_result=row["last_result"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _row_to_registered_group(row: sqlite3.Row) -> RegisteredGroup:
    container_config = None
    if row["container_config"]:
        raw = json.loads(row["container_config"])
        container_config = ContainerConfig(
            additional_mounts=[
                AdditionalMount(
                    host_path=m["hostPath"],
                    container_path=m.get("containerPath"),
                    readonly=m.get("readonly"),
                )
                for m in raw.get("additionalMounts", [])
            ] if raw.get("additionalMounts") else None,
            timeout=raw.get("timeout"),
        )

    return RegisteredGroup(
        name=row["name"],
        folder=row["folder"],
        trigger=row["trigger_pattern"],
        added_at=row["added_at"],
        container_config=container_config,
        requires_trigger=None if row["requires_trigger"] is None else row["requires_trigger"] == 1,
    )


def _container_config_to_dict(config: ContainerConfig) -> dict:
    result: dict = {}
    if config.additional_mounts:
        result["additionalMounts"] = [
            {
                "hostPath": m.host_path,
                **({"containerPath": m.container_path} if m.container_path else {}),
                **({"readonly": m.readonly} if m.readonly is not None else {}),
            }
            for m in config.additional_mounts
        ]
    if config.timeout is not None:
        result["timeout"] = config.timeout
    return result


# --- JSON migration ---


def _migrate_json_state() -> None:
    def migrate_file(filename: str):
        file_path = DATA_DIR / filename
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            file_path.rename(f"{file_path}.migrated")
            return data
        except Exception:
            return None

    # Migrate router_state.json
    router_state = migrate_file("router_state.json")
    if router_state:
        if router_state.get("last_timestamp"):
            set_router_state("last_timestamp", router_state["last_timestamp"])
        if router_state.get("last_agent_timestamp"):
            set_router_state(
                "last_agent_timestamp",
                json.dumps(router_state["last_agent_timestamp"]),
            )

    # Migrate sessions.json
    sessions = migrate_file("sessions.json")
    if sessions:
        for folder, session_id in sessions.items():
            set_session(folder, session_id)

    # Migrate registered_groups.json
    groups = migrate_file("registered_groups.json")
    if groups:
        for jid, group_data in groups.items():
            group = RegisteredGroup(
                name=group_data["name"],
                folder=group_data["folder"],
                trigger=group_data["trigger"],
                added_at=group_data["added_at"],
                container_config=None,
                requires_trigger=group_data.get("requiresTrigger"),
            )
            if group_data.get("containerConfig"):
                raw = group_data["containerConfig"]
                group.container_config = ContainerConfig(
                    additional_mounts=[
                        AdditionalMount(
                            host_path=m["hostPath"],
                            container_path=m.get("containerPath"),
                            readonly=m.get("readonly"),
                        )
                        for m in raw.get("additionalMounts", [])
                    ] if raw.get("additionalMounts") else None,
                    timeout=raw.get("timeout"),
                )
            set_registered_group(jid, group)
