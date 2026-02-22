import pytest
from slimclaw.db import (
    _init_test_database,
    create_task,
    get_all_tasks,
    get_registered_group,
    get_task_by_id,
    set_registered_group,
)
from slimclaw.ipc import process_task_ipc
from slimclaw.types import RegisteredGroup, ScheduledTask

MAIN_GROUP = RegisteredGroup(
    name="Main", folder="main", trigger="always",
    added_at="2024-01-01T00:00:00.000Z",
)
OTHER_GROUP = RegisteredGroup(
    name="Other", folder="other-group", trigger="@TARS",
    added_at="2024-01-01T00:00:00.000Z",
)
THIRD_GROUP = RegisteredGroup(
    name="Third", folder="third-group", trigger="@TARS",
    added_at="2024-01-01T00:00:00.000Z",
)


@pytest.fixture(autouse=True)
def setup():
    _init_test_database()

    global groups
    groups = {
        "main@g.us": MAIN_GROUP,
        "other@g.us": OTHER_GROUP,
        "third@g.us": THIRD_GROUP,
    }

    set_registered_group("main@g.us", MAIN_GROUP)
    set_registered_group("other@g.us", OTHER_GROUP)
    set_registered_group("third@g.us", THIRD_GROUP)


class MockDeps:
    async def send_message(self, jid, text):
        pass

    def registered_groups(self):
        return groups

    def register_group(self, jid, group):
        groups[jid] = group
        set_registered_group(jid, group)

    async def sync_group_metadata(self, force):
        pass

    def get_available_groups(self):
        return []

    def write_groups_snapshot(self, gf, im, ag, rj):
        pass


deps = MockDeps()


# --- schedule_task authorization ---


class TestScheduleTaskAuth:
    @pytest.mark.asyncio
    async def test_main_can_schedule_for_other(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "do something", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        tasks = get_all_tasks()
        assert len(tasks) == 1
        assert tasks[0].group_folder == "other-group"

    @pytest.mark.asyncio
    async def test_non_main_can_schedule_for_self(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "self task", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "targetJid": "other@g.us"},
            "other-group", False, deps,
        )
        tasks = get_all_tasks()
        assert len(tasks) == 1
        assert tasks[0].group_folder == "other-group"

    @pytest.mark.asyncio
    async def test_non_main_cannot_schedule_for_other(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "unauthorized", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "targetJid": "main@g.us"},
            "other-group", False, deps,
        )
        assert len(get_all_tasks()) == 0

    @pytest.mark.asyncio
    async def test_rejects_unregistered_target(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "no target", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "targetJid": "unknown@g.us"},
            "main", True, deps,
        )
        assert len(get_all_tasks()) == 0


# --- pause_task authorization ---


class TestPauseTaskAuth:
    @pytest.fixture(autouse=True)
    def create_tasks(self):
        create_task(ScheduledTask(
            id="task-main", group_folder="main", chat_jid="main@g.us",
            prompt="main task", schedule_type="once",
            schedule_value="2025-06-01T00:00:00.000Z", context_mode="isolated",
            next_run="2025-06-01T00:00:00.000Z", last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        create_task(ScheduledTask(
            id="task-other", group_folder="other-group", chat_jid="other@g.us",
            prompt="other task", schedule_type="once",
            schedule_value="2025-06-01T00:00:00.000Z", context_mode="isolated",
            next_run="2025-06-01T00:00:00.000Z", last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))

    @pytest.mark.asyncio
    async def test_main_can_pause_any(self):
        await process_task_ipc({"type": "pause_task", "taskId": "task-other"}, "main", True, deps)
        assert get_task_by_id("task-other").status == "paused"

    @pytest.mark.asyncio
    async def test_non_main_can_pause_own(self):
        await process_task_ipc({"type": "pause_task", "taskId": "task-other"}, "other-group", False, deps)
        assert get_task_by_id("task-other").status == "paused"

    @pytest.mark.asyncio
    async def test_non_main_cannot_pause_other(self):
        await process_task_ipc({"type": "pause_task", "taskId": "task-main"}, "other-group", False, deps)
        assert get_task_by_id("task-main").status == "active"


# --- resume_task authorization ---


class TestResumeTaskAuth:
    @pytest.fixture(autouse=True)
    def create_paused_task(self):
        create_task(ScheduledTask(
            id="task-paused", group_folder="other-group", chat_jid="other@g.us",
            prompt="paused task", schedule_type="once",
            schedule_value="2025-06-01T00:00:00.000Z", context_mode="isolated",
            next_run="2025-06-01T00:00:00.000Z", last_run=None, last_result=None,
            status="paused", created_at="2024-01-01T00:00:00.000Z",
        ))

    @pytest.mark.asyncio
    async def test_main_can_resume(self):
        await process_task_ipc({"type": "resume_task", "taskId": "task-paused"}, "main", True, deps)
        assert get_task_by_id("task-paused").status == "active"

    @pytest.mark.asyncio
    async def test_non_main_can_resume_own(self):
        await process_task_ipc({"type": "resume_task", "taskId": "task-paused"}, "other-group", False, deps)
        assert get_task_by_id("task-paused").status == "active"

    @pytest.mark.asyncio
    async def test_non_main_cannot_resume_other(self):
        await process_task_ipc({"type": "resume_task", "taskId": "task-paused"}, "third-group", False, deps)
        assert get_task_by_id("task-paused").status == "paused"


# --- cancel_task authorization ---


class TestCancelTaskAuth:
    @pytest.mark.asyncio
    async def test_main_can_cancel(self):
        create_task(ScheduledTask(
            id="task-to-cancel", group_folder="other-group", chat_jid="other@g.us",
            prompt="cancel me", schedule_type="once",
            schedule_value="2025-06-01T00:00:00.000Z", context_mode="isolated",
            next_run=None, last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        await process_task_ipc({"type": "cancel_task", "taskId": "task-to-cancel"}, "main", True, deps)
        assert get_task_by_id("task-to-cancel") is None

    @pytest.mark.asyncio
    async def test_non_main_can_cancel_own(self):
        create_task(ScheduledTask(
            id="task-own", group_folder="other-group", chat_jid="other@g.us",
            prompt="my task", schedule_type="once",
            schedule_value="2025-06-01T00:00:00.000Z", context_mode="isolated",
            next_run=None, last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        await process_task_ipc({"type": "cancel_task", "taskId": "task-own"}, "other-group", False, deps)
        assert get_task_by_id("task-own") is None

    @pytest.mark.asyncio
    async def test_non_main_cannot_cancel_other(self):
        create_task(ScheduledTask(
            id="task-foreign", group_folder="main", chat_jid="main@g.us",
            prompt="not yours", schedule_type="once",
            schedule_value="2025-06-01T00:00:00.000Z", context_mode="isolated",
            next_run=None, last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        await process_task_ipc({"type": "cancel_task", "taskId": "task-foreign"}, "other-group", False, deps)
        assert get_task_by_id("task-foreign") is not None


# --- register_group authorization ---


class TestRegisterGroupAuth:
    @pytest.mark.asyncio
    async def test_non_main_cannot_register(self):
        await process_task_ipc(
            {"type": "register_group", "jid": "new@g.us", "name": "New Group",
             "folder": "new-group", "trigger": "@TARS"},
            "other-group", False, deps,
        )
        assert groups.get("new@g.us") is None

    @pytest.mark.asyncio
    async def test_main_can_register(self):
        await process_task_ipc(
            {"type": "register_group", "jid": "new@g.us", "name": "New Group",
             "folder": "new-group", "trigger": "@TARS"},
            "main", True, deps,
        )
        group = get_registered_group("new@g.us")
        assert group is not None
        assert group.name == "New Group"
        assert group.folder == "new-group"

    @pytest.mark.asyncio
    async def test_rejects_missing_fields(self):
        await process_task_ipc(
            {"type": "register_group", "jid": "partial@g.us", "name": "Partial"},
            "main", True, deps,
        )
        assert get_registered_group("partial@g.us") is None


# --- IPC message authorization ---


class TestIpcMessageAuth:
    @staticmethod
    def _is_authorized(source_group, is_main, target_jid, registered):
        target = registered.get(target_jid)
        return is_main or (target is not None and target.folder == source_group)

    def test_main_can_send_to_any(self):
        assert self._is_authorized("main", True, "other@g.us", groups)
        assert self._is_authorized("main", True, "third@g.us", groups)

    def test_non_main_can_send_to_own(self):
        assert self._is_authorized("other-group", False, "other@g.us", groups)

    def test_non_main_cannot_send_to_other(self):
        assert not self._is_authorized("other-group", False, "main@g.us", groups)
        assert not self._is_authorized("other-group", False, "third@g.us", groups)

    def test_non_main_cannot_send_to_unregistered(self):
        assert not self._is_authorized("other-group", False, "unknown@g.us", groups)

    def test_main_can_send_to_unregistered(self):
        assert self._is_authorized("main", True, "unknown@g.us", groups)


# --- schedule_task schedule types ---


class TestScheduleTypes:
    @pytest.mark.asyncio
    async def test_cron_computes_next_run(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "cron task", "schedule_type": "cron",
             "schedule_value": "0 9 * * *", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        tasks = get_all_tasks()
        assert len(tasks) == 1
        assert tasks[0].schedule_type == "cron"
        assert tasks[0].next_run is not None

    @pytest.mark.asyncio
    async def test_rejects_invalid_cron(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "bad cron", "schedule_type": "cron",
             "schedule_value": "not a cron", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert len(get_all_tasks()) == 0

    @pytest.mark.asyncio
    async def test_interval_schedule(self):
        import time
        before = time.time()
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "interval task", "schedule_type": "interval",
             "schedule_value": "3600000", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        tasks = get_all_tasks()
        assert len(tasks) == 1
        assert tasks[0].schedule_type == "interval"

    @pytest.mark.asyncio
    async def test_rejects_non_numeric_interval(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "bad interval", "schedule_type": "interval",
             "schedule_value": "abc", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert len(get_all_tasks()) == 0

    @pytest.mark.asyncio
    async def test_rejects_zero_interval(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "zero interval", "schedule_type": "interval",
             "schedule_value": "0", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert len(get_all_tasks()) == 0

    @pytest.mark.asyncio
    async def test_rejects_invalid_once_timestamp(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "bad once", "schedule_type": "once",
             "schedule_value": "not-a-date", "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert len(get_all_tasks()) == 0


# --- context_mode ---


class TestContextMode:
    @pytest.mark.asyncio
    async def test_accepts_group(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "group ctx", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "context_mode": "group",
             "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert get_all_tasks()[0].context_mode == "group"

    @pytest.mark.asyncio
    async def test_accepts_isolated(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "isolated ctx", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "context_mode": "isolated",
             "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert get_all_tasks()[0].context_mode == "isolated"

    @pytest.mark.asyncio
    async def test_defaults_invalid_to_isolated(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "bad ctx", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z", "context_mode": "bogus",
             "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert get_all_tasks()[0].context_mode == "isolated"

    @pytest.mark.asyncio
    async def test_defaults_missing_to_isolated(self):
        await process_task_ipc(
            {"type": "schedule_task", "prompt": "no ctx", "schedule_type": "once",
             "schedule_value": "2025-06-01T00:00:00.000Z",
             "targetJid": "other@g.us"},
            "main", True, deps,
        )
        assert get_all_tasks()[0].context_mode == "isolated"
