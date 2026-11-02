"""Standing voice context — inject, and what VoiceAgent opens on."""

from __future__ import annotations

import inspect
import json

import httpx
import pytest

from grok_faf_voice.agent import (
    ON_FILE_HEADER,
    OPEN_STANDING_BYTES,
    REASONING_EFFORT,
    VOICE_MODEL,
    VoiceAgent,
    compose_open_instructions,
    compose_open_user_item,
    reasoning_for_session,
    voice_on_the_wire,
)
from grok_faf_voice.memory import LATENCY_BRIDGE_INSTRUCTIONS
from grok_faf_voice.standing import InjectError, fetch_inject


def test_voice_model_is_pinned():
    assert VOICE_MODEL == "grok-voice-3.0"
    assert VOICE_MODEL != "grok-voice-latest"
    assert REASONING_EFFORT == "none"
    source = inspect.getsource(VoiceAgent._launch)
    assert "model=VOICE_MODEL" in source
    assert "grok-voice-latest" not in source
    assert "mem.tools(session) if agent_self._etch else []" in source
    assert "reasoning_for_session()" in source
    assert "voice_on_the_wire(voice)" in source
    assert "recall_for_prompt" not in source
    assert "response.create" not in source


def test_reasoning_effort_is_set_on_purpose():
    built = reasoning_for_session()
    assert built.model_dump()["effort"] == "none"


def test_builtin_voices_go_out_lowercase():
    assert voice_on_the_wire("Ara") == "ara"
    assert voice_on_the_wire("EVE") == "eve"
    assert voice_on_the_wire("vluy2u1jtsif") == "vluy2u1jtsif"


def test_default_open_is_the_standing_string_only():
    text = "Know the project. Do not invent."
    assert compose_open_instructions(text) == text
    assert "etch_memory" not in compose_open_instructions(text)
    assert ON_FILE_HEADER not in compose_open_instructions(text)


def test_etch_opt_in_adds_the_bridge_not_the_soul():
    opened = compose_open_instructions("standing", etch=True)
    assert opened.startswith("standing")
    assert "prior etch" not in opened
    assert LATENCY_BRIDGE_INSTRUCTIONS in opened
    assert "etch_memory" not in compose_open_instructions("standing", etch=False)


def test_recalled_facts_are_a_user_item_not_instructions():
    item = compose_open_user_item("favorite color is cyan")
    assert item["type"] == "conversation.item.create"
    assert item["item"]["role"] == "user"
    text = item["item"]["content"][0]["text"]
    assert text.startswith(ON_FILE_HEADER)
    assert "favorite color is cyan" in text
    assert "response.create" not in json.dumps(item)
    opened = compose_open_instructions("standing string")
    assert "favorite color is cyan" not in opened
    assert ON_FILE_HEADER not in opened


def test_open_facts_fit_a_user_chat_message():
    from livekit.agents.llm import ChatContext

    item = compose_open_user_item("school run at 3")
    chat_ctx = ChatContext.empty()
    chat_ctx.add_message(role="user", content=item["item"]["content"][0]["text"])
    message = chat_ctx.messages()[0]
    assert str(message.role) == "user" or getattr(message.role, "value", None) == "user"
    assert ON_FILE_HEADER in _message_text(message)


def _message_text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return " ".join(part if isinstance(part, str) else str(part) for part in content)


def test_standing_string_is_capped_near_two_kilobytes():
    standing = "line of fact\n" * 400
    opened = compose_open_instructions(standing, project_prompt="Role: helper.")
    assert opened.startswith("Role: helper.")
    body = opened.split("\n\n", 1)[1]
    assert len(body.encode("utf-8")) <= OPEN_STANDING_BYTES
    assert len(standing.encode("utf-8")) > OPEN_STANDING_BYTES
    assert body.endswith("line of fact")


def test_cap_does_not_split_a_multibyte_character():
    standing = "é" * 3000
    capped = compose_open_instructions(standing)
    assert len(capped.encode("utf-8")) <= OPEN_STANDING_BYTES
    capped.encode("utf-8").decode("utf-8")


def test_default_agent_does_not_etch():
    agent = VoiceAgent(api_key="k", namepoint="wolfe26")
    assert agent._etch is False


async def test_fetch_inject_returns_the_served_string():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        body = json.loads(request.content.decode())
        assert body["model"] == VOICE_MODEL
        return httpx.Response(200, json={
            "instructions": "HELLO",
            "sha256": "abc",
            "bytes": 5,
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await fetch_inject(
            "k", model=VOICE_MODEL, url="https://mcpaas.live/api/voice/inject", client=client
        )
    assert data["instructions"] == "HELLO"
    assert data["sha256"] == "abc"


async def test_fetch_inject_refuses_a_non_200():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Voice key required.")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(InjectError):
            await fetch_inject("nope", url="https://mcpaas.live/api/voice/inject", client=client)
