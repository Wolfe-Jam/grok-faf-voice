"""A write happens only after the agent speaks the fact and the holder confirms."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from grok_faf_voice import FAFEtchError, FAFMemory, VoiceAgent
from grok_faf_voice.memory import SPOKEN_FACT
from grok_faf_voice.tools import (
    SAVE_SPOKEN,
    HolderConfirm,
    holder_confirmed,
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


class _Says:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, text: str) -> None:
        self.said.append(text)


def test_no_voice_id_gate():
    assert "voice_id" not in inspect.signature(VoiceAgent.__init__).parameters
    assert "voice_id" not in inspect.signature(FAFMemory.__init__).parameters


def test_confirm_is_not_a_model_tool():
    mem = FAFMemory("grok", api_key="k")
    names = {tool.info.name for tool in mem.tools(_Session())}
    assert "confirm_memory" not in names
    assert "holder_confirmed" not in inspect.signature(mem.confirm_memory).parameters


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


async def test_confirm_before_the_yes_does_not_save_or_block_it():
    """Live 2026-11-03: confirm ran before the stage and before Yes."""
    mem = FAFMemory("grok", api_key="k")
    with patch.object(mem, "etch", new_callable=AsyncMock, return_value="saved") as etch:
        early = await mem.confirm_memory("favorite color is cyan")
        assert early == "Nothing was saved."
        spoken = mem.stage_memory("favorite color is cyan")
        again = await mem.confirm_memory("favorite color is cyan")
        assert again == "Nothing was saved."
        assert mem.pending is not None
        etch.assert_not_called()
        session = _Session([_Msg("assistant", "Got it. " + spoken)])
        result = await HolderConfirm(mem, session).apply_final("Yes.")
    etch.assert_awaited_once()
    assert etch.await_args.args[0] == "favorite color is cyan"
    assert result == "saved"
    assert mem.pending is None


async def test_final_yes_saves():
    """A final yes saves. The yes is the event, not a history line."""
    mem = FAFMemory("grok", api_key="k")
    spoken = mem.stage_memory("favorite color is cyan")
    session = _Session([
        _Msg("user", "Please remember that my favorite color is cyan."),
        _Msg("assistant", "Got it. " + spoken),
    ])
    listener = HolderConfirm(mem, session)
    with patch.object(mem, "etch", new_callable=AsyncMock, return_value="saved") as etch:
        seen: list[str] = []

        async def _spy(
            transcript: str,
            *,
            messages: list | None = None,
            item_id: str | None = None,
        ) -> str:
            seen.append(transcript)
            return await HolderConfirm.apply_final(
                listener, transcript, messages=messages, item_id=item_id
            )

        listener.apply_final = _spy  # type: ignore[method-assign]
        listener._on_user_input(SimpleNamespace(transcript="Yes.", is_final=True))
        await asyncio.sleep(0)
    assert seen == ["Yes."]
    etch.assert_awaited_once()
    assert mem.pending is None
    assert mem.last_confirm_result == "saved"


async def test_interim_yes_does_not_save():
    mem = FAFMemory("grok", api_key="k")
    spoken = mem.stage_memory("favorite color is cyan")
    session = _Session([_Msg("assistant", spoken)])
    listener = HolderConfirm(mem, session)
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        listener._on_user_input(SimpleNamespace(transcript="Yes.", is_final=False))
        await asyncio.sleep(0)
    etch.assert_not_called()
    assert mem.pending is not None
    assert mem.pending["content"] == "favorite color is cyan"


async def test_no_drops_the_staged_fact():
    mem = FAFMemory("grok", api_key="k")
    spoken = mem.stage_memory("favorite color is cyan")
    session = _Says()
    listener = HolderConfirm(mem, session)
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        result = await listener.apply_final(
            "no",
            messages=[_Msg("assistant", spoken)],
        )
        later = await listener.apply_final(
            "Yes.",
            messages=[_Msg("assistant", spoken)],
        )
    etch.assert_not_called()
    assert result == "Nothing was saved."
    assert session.said == ["Nothing was saved."]
    assert mem.pending is None
    assert later == ""


async def test_final_yes_after_a_question_drops():
    """B1: the sentence has to end the assistant message."""
    mem = FAFMemory("grok", api_key="k")
    spoken = mem.stage_memory("favorite color is cyan")
    listener = HolderConfirm(mem, None)
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        result = await listener.apply_final(
            "Yes.",
            messages=[
                _Msg("assistant", spoken + " Also, want me to book the table?"),
            ],
        )
    etch.assert_not_called()
    assert result == "Nothing was saved."
    assert mem.pending is None


async def test_listener_keeps_a_shared_device_off_the_soul():
    mem = FAFMemory("grok", api_key="k", shared_device=True)
    spoken = mem.stage_memory("school run at 3")
    with patch.object(mem, "etch", new_callable=AsyncMock) as etch:
        result = await HolderConfirm(mem, None).apply_final(
            "Yes.",
            messages=[_Msg("assistant", spoken)],
        )
    etch.assert_not_called()
    assert result == "Held for this session only."


async def test_save_sentence_then_yes_merges():
    """Live order: the agent says the save sentence, then the holder says yes."""
    mem = FAFMemory("grok", api_key="k")

    async def _merge(*, strategy: str = "heuristic", **_):
        return {
            "promoted": 1,
            "discarded": 0,
            "strategy": strategy,
            "merged": 0,
            "kept_ephemeral": 0,
            "overall_notes": None,
        }

    with patch.object(mem, "merge", side_effect=_merge) as merge:
        result = await HolderConfirm(mem, None).apply_final(
            "Yes.",
            messages=[_Msg("assistant", SAVE_SPOKEN)],
        )
    merge.assert_awaited_once()
    assert result == "Saved 1 memories. 0 kept ephemeral."


async def test_merge_tool_before_the_yes_does_not_block_it():
    from grok_faf_voice.tools import make_merge_tool

    mem = FAFMemory("grok", api_key="k")

    async def _merge(*, strategy: str = "heuristic", **_):
        return {
            "promoted": 1,
            "discarded": 0,
            "strategy": strategy,
            "merged": 0,
            "kept_ephemeral": 0,
            "overall_notes": None,
        }

    class _Quiet:
        async def say(self, text: str) -> None:
            return None

    with patch.object(mem, "merge", side_effect=_merge) as merge:
        staged = await make_merge_tool(mem, _Quiet())._func(None)
        assert SAVE_SPOKEN in staged
        merge.assert_not_called()
        result = await HolderConfirm(mem, _Quiet()).apply_final(
            "Yes.",
            messages=[_Msg("assistant", SAVE_SPOKEN)],
        )
    merge.assert_awaited_once()
    assert result.startswith("Saved 1")


async def test_request_final_does_not_drop_or_block_the_later_yes():
    """Live 2026-11-03: the request's final arrived after the tool.

    The tool ran before that transcript was the latest user line, and
    the socket then delivered it as a final, including after the
    confirmation sentence. That final must not save and must not drop
    the stage. The holder's later ``Yes.`` still saves. A second final
    of that yes does not write again.
    """
    mem = FAFMemory("grok", api_key="k")
    session = _Session([_Msg("assistant", "Perfect, it's saved.")])
    listener = HolderConfirm(mem, session)
    mem._confirm = listener
    request = "Please remember that my favorite color is cyan."
    listener._on_user_input(
        SimpleNamespace(transcript=request, is_final=False, item_id="req-1")
    )
    spoken = mem.stage_memory("favorite color is cyan")

    before = await listener.apply_final(
        request,
        item_id="req-1",
        messages=list(session.history.messages()),
    )
    after = await listener.apply_final(
        request,
        item_id="req-1",
        messages=[_Msg("assistant", "Got it. " + spoken)],
    )
    assert before == ""
    assert after == ""
    assert mem.pending is not None

    with patch.object(mem, "etch", new_callable=AsyncMock, return_value="saved") as etch:
        saved = await listener.apply_final(
            "Yes.",
            item_id="yes-1",
            messages=[_Msg("assistant", "Got it. " + spoken)],
        )
        interim = listener._on_user_input(
            SimpleNamespace(transcript="Yes.", is_final=False, item_id="yes-2")
        )
        again = await listener.apply_final(
            "Yes",
            item_id="yes-1",
            messages=[_Msg("assistant", "Got it. " + spoken)],
        )
    assert interim is None
    etch.assert_awaited_once()
    assert saved == "saved"
    assert again == ""
    assert mem.pending is None


async def test_merge_request_final_does_not_drop_or_block_the_later_yes():
    """The save request flushed after merge_now must not disarm the save."""
    mem = FAFMemory("grok", api_key="k")
    session = _Session([_Msg("assistant", "Perfect, it's saved.")])
    listener = HolderConfirm(mem, session)
    mem._confirm = listener
    request = "Please save what we discussed."
    listener._on_user_input(
        SimpleNamespace(transcript=request, is_final=False, item_id="req-1")
    )
    mem.arm_merge()

    before = await listener.apply_final(
        request,
        item_id="req-1",
        messages=list(session.history.messages()),
    )
    after = await listener.apply_final(
        request,
        item_id="req-1",
        messages=[_Msg("assistant", SAVE_SPOKEN)],
    )
    assert before == ""
    assert after == ""
    assert mem.merge_armed is True

    async def _merge(*, strategy: str = "heuristic", **_):
        return {
            "promoted": 1,
            "discarded": 0,
            "strategy": strategy,
            "merged": 0,
            "kept_ephemeral": 0,
            "overall_notes": None,
        }

    with patch.object(mem, "merge", side_effect=_merge) as merge:
        saved = await listener.apply_final(
            "Yes.",
            item_id="yes-1",
            messages=[_Msg("assistant", SAVE_SPOKEN)],
        )
        again = await listener.apply_final(
            "Yes",
            item_id="yes-2",
            messages=[_Msg("assistant", SAVE_SPOKEN)],
        )
    merge.assert_awaited_once()
    assert saved.startswith("Saved 1")
    assert again == ""
    assert mem.merge_armed is False


async def test_spoken_etch_call_stages_and_a_later_yes_saves():
    """Live 2026-11-03: the model spoke etch_memory instead of calling it."""
    mem = FAFMemory("grok", api_key="k")
    said: list[str] = []

    class _Talks:
        def __init__(self) -> None:
            self.history = _Hist([
                _Msg("user", "Please remember that my favorite color is cyan."),
            ])

        def say(self, text: str, **_kwargs: object) -> None:
            said.append(text)
            self.history._messages.append(_Msg("assistant", text))

        def generate_reply(self, **_kwargs: object) -> None:
            raise AssertionError("spoken etch must not also nudge")

    listener = HolderConfirm(mem, _Talks())
    listener._open_text = "Please remember that my favorite color is cyan."
    listener._open_item = "req-1"
    spoken_call = (
        "Got it. <tool_call>etch_memory<arg_key>content</arg_key>"
        "<arg_value>favorite color is cyan</arg_value></tool_call>"
    )
    listener._on_item(SimpleNamespace(item=_Msg("assistant", spoken_call)))
    assert mem.pending is not None
    assert mem.pending["content"] == "favorite color is cyan"
    spoken = mem.pending["spoken"]
    assert said == [spoken]

    with patch.object(mem, "etch", new_callable=AsyncMock, return_value="saved") as etch:
        echo = await listener.apply_final(
            "Please remember that my favorite color is cyan.",
            item_id="req-1",
            messages=[_Msg("assistant", spoken)],
        )
        saved = await listener.apply_final(
            "Yes.",
            item_id="yes-1",
            messages=[_Msg("assistant", spoken)],
        )
    etch.assert_awaited_once()
    assert echo == ""
    assert saved == "saved"
    assert mem.pending is None


def test_bridge_without_a_tool_asks_once_for_etch():
    """Live 2026-11-03: the model said "Got it." and never called etch."""
    mem = FAFMemory("grok", api_key="k")
    calls: list[dict] = []

    class _Talks:
        def __init__(self) -> None:
            self.history = _Hist([
                _Msg("user", "Please remember that my favorite color is cyan."),
                _Msg("assistant", "Got it."),
            ])

        def generate_reply(self, **kwargs: object) -> None:
            calls.append(kwargs)

    listener = HolderConfirm(mem, _Talks())
    item = _Msg("assistant", "Got it.")
    listener._on_item(SimpleNamespace(item=item))
    listener._on_item(SimpleNamespace(item=item))
    assert len(calls) == 1
    assert "etch_memory" in str(calls[0].get("instructions"))
    assert mem.pending is None


def test_no_etch_nudge_when_staged_or_the_user_did_not_ask():
    mem = FAFMemory("grok", api_key="k")
    calls: list[dict] = []

    class _Talks:
        def __init__(self, messages: list) -> None:
            self.history = _Hist(messages)

        def generate_reply(self, **kwargs: object) -> None:
            calls.append(kwargs)

    listener = HolderConfirm(
        mem,
        _Talks([_Msg("user", "Please remember that my favorite color is cyan.")]),
    )
    mem.stage_memory("favorite color is cyan")
    listener._on_item(SimpleNamespace(item=_Msg("assistant", "Got it.")))
    mem.drop_pending()
    other = HolderConfirm(
        mem,
        _Talks([_Msg("user", "Please save what we discussed.")]),
    )
    other._on_item(SimpleNamespace(item=_Msg("assistant", SAVE_SPOKEN)))
    plain = HolderConfirm(mem, _Session([
        _Msg("user", "Please remember that my favorite color is cyan."),
    ]))
    plain._on_item(SimpleNamespace(item=_Msg("assistant", "Got it.")))
    assert calls == []
    assert mem.pending is None


def test_instructions_do_not_quote_the_confirmation_sentence():
    """The model learns the sentence from the tool result, not the prompt."""
    from grok_faf_voice.memory import LATENCY_BRIDGE_INSTRUCTIONS

    assert "I'll remember this:" not in LATENCY_BRIDGE_INSTRUCTIONS
    assert "I'll save what we discussed." not in LATENCY_BRIDGE_INSTRUCTIONS
    assert "Do not invent a sentence." in LATENCY_BRIDGE_INSTRUCTIONS
    mem = FAFMemory("grok", api_key="k")
    for tool in mem.tools(_Session()):
        description = str(getattr(tool.info, "description", "") or "")
        assert "I'll remember this:" not in description
        assert "I'll save what we discussed." not in description


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


def test_comma_sentence_and_yes_prefix_confirm():
    """Punctuation folds. A yes may continue unless it contains no or don't."""
    spoken = SPOKEN_FACT.format(content="favorite color is cyan")
    messages = [
        _Msg("assistant", "I'll remember this, favorite color is cyan."),
        _Msg("user", "Yes, please do."),
    ]
    assert holder_confirmed(messages, spoken) is True
    refused = [
        _Msg("assistant", "I'll remember this, favorite color is cyan."),
        _Msg("user", "Yes, don't"),
    ]
    assert holder_confirmed(refused, spoken) is False


def test_live_transcript_lines_confirm():
    """Lines from the 2026-11-03 grok-voice-3.0 session."""
    spoken = SPOKEN_FACT.format(content="favorite color is cyan")
    first = [
        _Msg("assistant", "Got it. I'll remember this: favorite color is cyan."),
        _Msg("user", "Yes."),
    ]
    retry = [
        _Msg("assistant", "I'll remember this: favorite color is cyan."),
        _Msg("user", "Yes."),
    ]
    save = [
        _Msg("assistant", "I'll save what we discussed."),
        _Msg("user", "Yes."),
    ]
    assert holder_confirmed(first, spoken) is True
    assert holder_confirmed(retry, spoken) is True
    assert holder_confirmed(save, SAVE_SPOKEN) is True


def test_stage_strips_one_trailing_period():
    mem = FAFMemory("grok", api_key="k")
    spoken = mem.stage_memory("favorite color is cyan.")
    assert spoken == "I'll remember this: favorite color is cyan."
    assert mem.pending["content"] == "favorite color is cyan"


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
