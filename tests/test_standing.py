"""Standing voice context — inject, and what VoiceAgent opens on."""

from __future__ import annotations

import inspect
import json

import httpx
import pytest

from grok_faf_voice.agent import (
    VOICE_MODEL,
    VoiceAgent,
    compose_open_instructions,
)
from grok_faf_voice.memory import LATENCY_BRIDGE_INSTRUCTIONS
from grok_faf_voice.standing import InjectError, fetch_inject


def test_voice_model_is_pinned():
    assert VOICE_MODEL == "grok-voice-think-fast-2.0"
    assert VOICE_MODEL != "grok-voice-latest"
    source = inspect.getsource(VoiceAgent._launch)
    assert "model=VOICE_MODEL" in source
    assert "grok-voice-latest" not in source
    assert "tools=mem.tools(session) if agent_self._etch else []" in source


def test_default_open_is_the_standing_string_only():
    text = "Know the project. Do not invent."
    assert compose_open_instructions(text) == text
    assert "etch_memory" not in compose_open_instructions(text)


def test_etch_opt_in_adds_the_log_and_the_bridge():
    opened = compose_open_instructions(
        "standing",
        recall="prior etch",
        etch=True,
    )
    assert opened.startswith("standing")
    assert "prior etch" in opened
    assert LATENCY_BRIDGE_INSTRUCTIONS in opened
    assert "etch_memory" not in compose_open_instructions("standing", etch=False)


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
        data = await fetch_inject("k", model=VOICE_MODEL, url="https://mcpaas.live/api/voice/inject", client=client)
    assert data["instructions"] == "HELLO"
    assert data["sha256"] == "abc"


async def test_fetch_inject_refuses_a_non_200():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Voice key required.")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(InjectError):
            await fetch_inject("nope", url="https://mcpaas.live/api/voice/inject", client=client)
