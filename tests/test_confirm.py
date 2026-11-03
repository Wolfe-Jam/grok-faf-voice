"""A write happens only after the agent speaks the fact and the holder confirms."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

from grok_faf_voice import FAFEtchError, FAFMemory, VoiceAgent
from grok_faf_voice.memory import SPOKEN_FACT
from grok_faf_voice.tools import (
    SAVE_SPOKEN,
    holder_confirmed,
    make_confirm_tool,
    make_etch_tool,
    make_paralinguistic_tool,
)


class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _Hist:
    def __init__(self, messages: list) -> None:
        self._messages = messages

    def messages(self) -> list:
        return self._messages


class _Session:
    def __init__(self, messages: list | None = None) -> None:
        self.history = _Hist(messages or [])


class _Ctx:
    def __init__(self, messages: list | None = None) -> None:
        self.session = _Session(messages)


def test_no_voice_id_gate():
    assert "voice_id" not in inspect.signature(VoiceAgent.__init__).parameters
    assert "voice_id" not in inspect.signature(FAFMemory.__init__).parameters


def test_confirm_tool_has_no_holder_flag():
    tool = make_confirm_tool(FAFMemory("grok", api_key="k"))
    assert "holder_confirmed" not in inspect.signature(tool._func).parameters


async def test_etch_tool_stages_and_does_not_write():
    mem = FAFMemory("grok", api_key="k")
    tool = make_etch_tool(mem)
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        result = await tool._func(_Ctx(), "favorite color is cyan")
    etch.assert_not_called()
    assert mem.pending is not None
    assert mem.pending["content"] == "favorite color is cyan"
    assert "I'll remember this: favorite color is cyan." in result


async def test_paralinguistic_tool_stages():
    mem = FAFMemory("grok", api_key="k")
    tool = make_paralinguistic_tool(mem)
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        await tool._func(_Ctx(), "tone", "frustrated", "checkout")
    etch.assert_not_called()
    assert mem.pending is not None
    assert mem.pending["type"] == "paralinguistic"
    assert mem.pending["marker_type"] == "tone"


async def test_confirm_without_a_transcript_does_not_write():
    mem = FAFMemory("grok", api_key="k")
    mem.stage_memory("favorite color is cyan")
    tool = make_confirm_tool(mem)
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        result = await tool._func(_Ctx([]), "favorite color is cyan")
    etch.assert_not_called()
    assert result.startswith("Not saved")
    assert mem.pending is not None


async def test_confirm_after_spoken_fact_and_yes_writes():
    mem = FAFMemory("grok", api_key="k")
    spoken = mem.stage_memory("favorite color is cyan")
    tool = make_confirm_tool(mem)
    messages = [_Msg("assistant", spoken), _Msg("user", "yes")]
    with patch.object(mem, "etch", new_callable=AsyncMock, return_value="saved") as etch:
        result = await tool._func(_Ctx(messages), "favorite color is cyan")
    etch.assert_awaited_once()
    assert etch.await_args.args[0] == "favorite color is cyan"
    assert result == "saved"
    assert mem.pending is None


async def test_yes_before_the_sentence_is_not_confirmation():
    spoken = "I'll remember this: favorite color is cyan."
    messages = [_Msg("user", "yes"), _Msg("assistant", spoken)]
    assert holder_confirmed(messages, spoken) is False


def test_a_question_after_the_sentence_is_not_confirmation():
    """The yes answers the question that follows the fact sentence."""
    spoken = SPOKEN_FACT.format(content="favorite color is cyan")
    messages = [
        _Msg("assistant", spoken + " Also, want me to save everything we discussed?"),
        _Msg("user", "yes"),
    ]
    assert holder_confirmed(messages, spoken) is False


def test_live_bridge_and_yes_period_confirm():
    """Live 2026-11-03: bridge prefix, sentence at the end, user 'Yes.'"""
    spoken = SPOKEN_FACT.format(content="favorite color is cyan")
    messages = [
        _Msg("assistant", "Got it. " + spoken),
        _Msg("user", "Yes."),
    ]
    assert holder_confirmed(messages, spoken) is True
    assert holder_confirmed(messages[:1], spoken) is False


def test_live_yes_with_extra_words_does_not_confirm():
    """The spoken yes was 'Yes.' A longer yes was not in that transcript."""
    spoken = SPOKEN_FACT.format(content="favorite color is cyan")
    messages = [
        _Msg("assistant", spoken),
        _Msg("user", "Yes, please do."),
    ]
    assert holder_confirmed(messages, spoken) is False


def test_save_sentence_must_end_the_assistant_message():
    messages = [
        _Msg("assistant", SAVE_SPOKEN + " Also, want me to book the table?"),
        _Msg("user", "Yes."),
    ]
    assert holder_confirmed(messages, SAVE_SPOKEN) is False
    exact = [_Msg("assistant", SAVE_SPOKEN), _Msg("user", "Yes.")]
    assert holder_confirmed(exact, SAVE_SPOKEN) is True


def test_a_later_yes_does_not_confirm_an_older_fact():
    """The yes was to the booking, not to the PIN hint."""
    spoken = "I'll remember this: my PIN hint is blue."
    messages = [
        _Msg("assistant", spoken),
        _Msg("user", "no, don't"),
        _Msg("assistant", "Want me to book the table?"),
        _Msg("user", "yes"),
    ]
    assert holder_confirmed(messages, spoken) is False


async def test_commit_refuses_a_different_fact():
    mem = FAFMemory("grok", api_key="k")
    mem.stage_memory("favorite color is cyan")
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        try:
            await mem.commit_staged("a different fact", holder_confirmed=True)
        except FAFEtchError:
            pass
        else:
            raise AssertionError("mismatched fact was saved")
    etch.assert_not_called()
    assert mem.pending["content"] == "favorite color is cyan"


async def test_commit_refuses_without_confirmation():
    mem = FAFMemory("grok", api_key="k")
    mem.stage_memory("favorite color is cyan")
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        try:
            await mem.commit_staged("favorite color is cyan", holder_confirmed=False)
        except FAFEtchError:
            pass
        else:
            raise AssertionError("unconfirmed fact was saved")
    etch.assert_not_called()


async def test_shared_device_does_not_etch():
    await _assert_session_only(shared_device=True)


async def test_other_speaker_does_not_etch():
    await _assert_session_only(speaker_is_holder=False)


async def test_child_may_be_speaking_does_not_etch():
    await _assert_session_only(child_may_be_speaking=True)


async def _assert_session_only(**flags):
    mem = FAFMemory("grok", api_key="k", **flags)
    assert mem.retention() == "session-only"
    mem.stage_memory("school run at 3")
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        result = await mem.commit_staged("school run at 3", holder_confirmed=True)
    etch.assert_not_called()
    assert result == "Held for this session only."
    entry = mem.scratchpad.get_entry("session-only:1")
    assert entry is not None
    assert entry.priority == "ephemeral"
    assert entry.tag == "session-only"
    assert entry.value == "school run at 3"


def test_holder_on_their_own_device_is_durable():
    mem = FAFMemory("grok", api_key="k")
    assert mem.retention() == "durable"
