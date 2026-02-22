import pytest
from slimclaw.db import (
    _init_test_database,
    create_task,
    delete_task,
    get_all_chats,
    get_messages_since,
    get_new_messages,
    get_task_by_id,
    store_chat_metadata,
    store_message,
    update_task,
)
from slimclaw.types import NewMessage, ScheduledTask


@pytest.fixture(autouse=True)
def fresh_db():
    _init_test_database()


def _store(**overrides):
    defaults = dict(
        is_from_me=False,
        is_bot_message=False,
    )
    defaults.update(overrides)
    store_message(NewMessage(**defaults))


# --- storeMessage ---


class TestStoreMessage:
    def test_stores_and_retrieves(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        _store(
            id="msg-1",
            chat_jid="group@g.us",
            sender="123@s.whatsapp.net",
            sender_name="Alice",
            content="hello world",
            timestamp="2024-01-01T00:00:01.000Z",
        )
        messages = get_messages_since("group@g.us", "2024-01-01T00:00:00.000Z", "Andy")
        assert len(messages) == 1
        assert messages[0].id == "msg-1"
        assert messages[0].sender == "123@s.whatsapp.net"
        assert messages[0].sender_name == "Alice"
        assert messages[0].content == "hello world"

    def test_stores_empty_content(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        _store(
            id="msg-2",
            chat_jid="group@g.us",
            sender="111@s.whatsapp.net",
            sender_name="Dave",
            content="",
            timestamp="2024-01-01T00:00:04.000Z",
        )
        messages = get_messages_since("group@g.us", "2024-01-01T00:00:00.000Z", "Andy")
        assert len(messages) == 1
        assert messages[0].content == ""

    def test_stores_is_from_me(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        _store(
            id="msg-3",
            chat_jid="group@g.us",
            sender="me@s.whatsapp.net",
            sender_name="Me",
            content="my message",
            timestamp="2024-01-01T00:00:05.000Z",
            is_from_me=True,
        )
        messages = get_messages_since("group@g.us", "2024-01-01T00:00:00.000Z", "Andy")
        assert len(messages) == 1

    def test_upserts_on_duplicate(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        _store(
            id="msg-dup",
            chat_jid="group@g.us",
            sender="123@s.whatsapp.net",
            sender_name="Alice",
            content="original",
            timestamp="2024-01-01T00:00:01.000Z",
        )
        _store(
            id="msg-dup",
            chat_jid="group@g.us",
            sender="123@s.whatsapp.net",
            sender_name="Alice",
            content="updated",
            timestamp="2024-01-01T00:00:01.000Z",
        )
        messages = get_messages_since("group@g.us", "2024-01-01T00:00:00.000Z", "Andy")
        assert len(messages) == 1
        assert messages[0].content == "updated"


# --- getMessagesSince ---


class TestGetMessagesSince:
    @pytest.fixture(autouse=True)
    def setup_messages(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        _store(id="m1", chat_jid="group@g.us", sender="Alice@s.whatsapp.net", sender_name="Alice", content="first", timestamp="2024-01-01T00:00:01.000Z")
        _store(id="m2", chat_jid="group@g.us", sender="Bob@s.whatsapp.net", sender_name="Bob", content="second", timestamp="2024-01-01T00:00:02.000Z")
        store_message(NewMessage(id="m3", chat_jid="group@g.us", sender="Bot@s.whatsapp.net", sender_name="Bot", content="bot reply", timestamp="2024-01-01T00:00:03.000Z", is_bot_message=True))
        _store(id="m4", chat_jid="group@g.us", sender="Carol@s.whatsapp.net", sender_name="Carol", content="third", timestamp="2024-01-01T00:00:04.000Z")

    def test_returns_messages_after_timestamp(self):
        msgs = get_messages_since("group@g.us", "2024-01-01T00:00:02.000Z", "Andy")
        assert len(msgs) == 1
        assert msgs[0].content == "third"

    def test_excludes_bot_messages(self):
        msgs = get_messages_since("group@g.us", "2024-01-01T00:00:00.000Z", "Andy")
        bot_msgs = [m for m in msgs if m.content == "bot reply"]
        assert len(bot_msgs) == 0

    def test_returns_all_when_empty_timestamp(self):
        msgs = get_messages_since("group@g.us", "", "Andy")
        assert len(msgs) == 3

    def test_filters_pre_migration_bot_messages(self):
        _store(id="m5", chat_jid="group@g.us", sender="Bot@s.whatsapp.net", sender_name="Bot", content="Andy: old bot reply", timestamp="2024-01-01T00:00:05.000Z")
        msgs = get_messages_since("group@g.us", "2024-01-01T00:00:04.000Z", "Andy")
        assert len(msgs) == 0


# --- getNewMessages ---


class TestGetNewMessages:
    @pytest.fixture(autouse=True)
    def setup_messages(self):
        store_chat_metadata("group1@g.us", "2024-01-01T00:00:00.000Z")
        store_chat_metadata("group2@g.us", "2024-01-01T00:00:00.000Z")
        _store(id="a1", chat_jid="group1@g.us", sender="user@s.whatsapp.net", sender_name="User", content="g1 msg1", timestamp="2024-01-01T00:00:01.000Z")
        _store(id="a2", chat_jid="group2@g.us", sender="user@s.whatsapp.net", sender_name="User", content="g2 msg1", timestamp="2024-01-01T00:00:02.000Z")
        store_message(NewMessage(id="a3", chat_jid="group1@g.us", sender="user@s.whatsapp.net", sender_name="User", content="bot reply", timestamp="2024-01-01T00:00:03.000Z", is_bot_message=True))
        _store(id="a4", chat_jid="group1@g.us", sender="user@s.whatsapp.net", sender_name="User", content="g1 msg2", timestamp="2024-01-01T00:00:04.000Z")

    def test_returns_new_messages_across_groups(self):
        messages, new_ts = get_new_messages(["group1@g.us", "group2@g.us"], "2024-01-01T00:00:00.000Z", "Andy")
        assert len(messages) == 3
        assert new_ts == "2024-01-01T00:00:04.000Z"

    def test_filters_by_timestamp(self):
        messages, _ = get_new_messages(["group1@g.us", "group2@g.us"], "2024-01-01T00:00:02.000Z", "Andy")
        assert len(messages) == 1
        assert messages[0].content == "g1 msg2"

    def test_returns_empty_for_no_groups(self):
        messages, new_ts = get_new_messages([], "", "Andy")
        assert len(messages) == 0
        assert new_ts == ""


# --- storeChatMetadata ---


class TestStoreChatMetadata:
    def test_stores_with_jid_as_default_name(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        chats = get_all_chats()
        assert len(chats) == 1
        assert chats[0].jid == "group@g.us"
        assert chats[0].name == "group@g.us"

    def test_stores_with_explicit_name(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z", "My Group")
        chats = get_all_chats()
        assert chats[0].name == "My Group"

    def test_updates_name(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:00.000Z")
        store_chat_metadata("group@g.us", "2024-01-01T00:00:01.000Z", "Updated Name")
        chats = get_all_chats()
        assert len(chats) == 1
        assert chats[0].name == "Updated Name"

    def test_preserves_newer_timestamp(self):
        store_chat_metadata("group@g.us", "2024-01-01T00:00:05.000Z")
        store_chat_metadata("group@g.us", "2024-01-01T00:00:01.000Z")
        chats = get_all_chats()
        assert chats[0].last_message_time == "2024-01-01T00:00:05.000Z"


# --- Task CRUD ---


class TestTaskCrud:
    def test_creates_and_retrieves(self):
        create_task(ScheduledTask(
            id="task-1", group_folder="main", chat_jid="group@g.us",
            prompt="do something", schedule_type="once",
            schedule_value="2024-06-01T00:00:00.000Z", context_mode="isolated",
            next_run="2024-06-01T00:00:00.000Z", last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        task = get_task_by_id("task-1")
        assert task is not None
        assert task.prompt == "do something"
        assert task.status == "active"

    def test_updates_status(self):
        create_task(ScheduledTask(
            id="task-2", group_folder="main", chat_jid="group@g.us",
            prompt="test", schedule_type="once",
            schedule_value="2024-06-01T00:00:00.000Z", context_mode="isolated",
            next_run=None, last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        update_task("task-2", status="paused")
        assert get_task_by_id("task-2").status == "paused"

    def test_deletes_task(self):
        create_task(ScheduledTask(
            id="task-3", group_folder="main", chat_jid="group@g.us",
            prompt="delete me", schedule_type="once",
            schedule_value="2024-06-01T00:00:00.000Z", context_mode="isolated",
            next_run=None, last_run=None, last_result=None,
            status="active", created_at="2024-01-01T00:00:00.000Z",
        ))
        delete_task("task-3")
        assert get_task_by_id("task-3") is None
