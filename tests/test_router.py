import pytest
from slimclaw.config import ASSISTANT_NAME, TRIGGER_PATTERN
from slimclaw.router import escape_xml, format_messages, format_outbound, strip_internal_tags
from slimclaw.types import NewMessage


def make_msg(**overrides) -> NewMessage:
    defaults = dict(
        id="1",
        chat_jid="group@g.us",
        sender="123@s.whatsapp.net",
        sender_name="Alice",
        content="hello",
        timestamp="2024-01-01T00:00:00.000Z",
    )
    defaults.update(overrides)
    return NewMessage(**defaults)


# --- escapeXml ---


class TestEscapeXml:
    def test_escapes_ampersands(self):
        assert escape_xml("a & b") == "a &amp; b"

    def test_escapes_less_than(self):
        assert escape_xml("a < b") == "a &lt; b"

    def test_escapes_greater_than(self):
        assert escape_xml("a > b") == "a &gt; b"

    def test_escapes_double_quotes(self):
        assert escape_xml('"hello"') == "&quot;hello&quot;"

    def test_handles_multiple_special_chars(self):
        assert escape_xml('a & b < c > d "e"') == 'a &amp; b &lt; c &gt; d &quot;e&quot;'

    def test_passes_through_normal_strings(self):
        assert escape_xml("hello world") == "hello world"

    def test_handles_empty_string(self):
        assert escape_xml("") == ""


# --- formatMessages ---


class TestFormatMessages:
    def test_formats_single_message(self):
        result = format_messages([make_msg()])
        assert result == (
            "<messages>\n"
            '<message sender="Alice" time="2024-01-01T00:00:00.000Z">hello</message>\n'
            "</messages>"
        )

    def test_formats_multiple_messages(self):
        msgs = [
            make_msg(id="1", sender_name="Alice", content="hi", timestamp="t1"),
            make_msg(id="2", sender_name="Bob", content="hey", timestamp="t2"),
        ]
        result = format_messages(msgs)
        assert 'sender="Alice"' in result
        assert 'sender="Bob"' in result
        assert ">hi</message>" in result
        assert ">hey</message>" in result

    def test_escapes_sender_names(self):
        result = format_messages([make_msg(sender_name="A & B <Co>")])
        assert 'sender="A &amp; B &lt;Co&gt;"' in result

    def test_escapes_content(self):
        result = format_messages([make_msg(content='<script>alert("xss")</script>')])
        assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in result

    def test_handles_empty_array(self):
        result = format_messages([])
        assert result == "<messages>\n\n</messages>"


# --- TRIGGER_PATTERN ---


class TestTriggerPattern:
    def test_matches_at_start(self):
        assert TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME} hello")

    def test_matches_case_insensitively(self):
        assert TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME.lower()} hello")
        assert TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME.upper()} hello")

    def test_does_not_match_mid_message(self):
        assert not TRIGGER_PATTERN.search(f"hello @{ASSISTANT_NAME}")

    def test_does_not_match_partial_name(self):
        assert not TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME}extra hello")

    def test_matches_before_apostrophe(self):
        assert TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME}'s thing")

    def test_matches_alone(self):
        assert TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME}")

    def test_matches_with_leading_whitespace_after_trim(self):
        assert TRIGGER_PATTERN.search(f"@{ASSISTANT_NAME} hey".strip())


# --- stripInternalTags ---


class TestStripInternalTags:
    def test_strips_single_line(self):
        assert strip_internal_tags("hello <internal>secret</internal> world") == "hello  world"

    def test_strips_multi_line(self):
        assert strip_internal_tags("hello <internal>\nsecret\nstuff\n</internal> world") == "hello  world"

    def test_strips_multiple_blocks(self):
        assert strip_internal_tags("<internal>a</internal>hello<internal>b</internal>") == "hello"

    def test_returns_empty_for_only_internal(self):
        assert strip_internal_tags("<internal>only this</internal>") == ""


# --- formatOutbound ---


class TestFormatOutbound:
    def test_returns_text(self):
        assert format_outbound("hello world") == "hello world"

    def test_returns_empty_for_all_internal(self):
        assert format_outbound("<internal>hidden</internal>") == ""

    def test_strips_internal_tags(self):
        assert format_outbound("<internal>thinking</internal>The answer is 42") == "The answer is 42"


# --- Trigger gating with requiresTrigger ---


class TestTriggerGating:
    def _should_require_trigger(self, is_main: bool, requires_trigger) -> bool:
        return not is_main and requires_trigger is not False

    def _should_process(self, is_main: bool, requires_trigger, messages: list[NewMessage]) -> bool:
        if not self._should_require_trigger(is_main, requires_trigger):
            return True
        return any(TRIGGER_PATTERN.search(m.content.strip()) for m in messages)

    def test_main_always_processes(self):
        msgs = [make_msg(content="hello no trigger")]
        assert self._should_process(True, None, msgs)

    def test_main_processes_with_requires_trigger_true(self):
        msgs = [make_msg(content="hello no trigger")]
        assert self._should_process(True, True, msgs)

    def test_non_main_undefined_requires_trigger(self):
        msgs = [make_msg(content="hello no trigger")]
        assert not self._should_process(False, None, msgs)

    def test_non_main_true_requires_trigger(self):
        msgs = [make_msg(content="hello no trigger")]
        assert not self._should_process(False, True, msgs)

    def test_non_main_with_trigger_present(self):
        msgs = [make_msg(content=f"@{ASSISTANT_NAME} do something")]
        assert self._should_process(False, True, msgs)

    def test_non_main_false_always_processes(self):
        msgs = [make_msg(content="hello no trigger")]
        assert self._should_process(False, False, msgs)
