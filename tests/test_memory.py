"""Smoke tests for FAFMemory.

Offline tests verify the public surface, payload shape, and
scratchpad composition. Network-marked tests round-trip live
against MCPaaS using the `grok` dev soul.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from grok_faf_voice import FAFMemory, Scratchpad


def test_import():
    """FAFMemory is importable from the package root."""
    assert FAFMemory is not None


def test_instantiation():
    """FAFMemory accepts a soul name."""
    mem = FAFMemory("grok")
    assert mem.soul == "grok"


def test_token_from_env():
    """FAFMemory reads MCPAAS_TOKEN from env when no token arg given."""
    with patch.dict(os.environ, {"MCPAAS_TOKEN": "env-token-123"}):
        mem = FAFMemory("grok")
        assert mem._token == "env-token-123"


def test_token_constructor_overrides_env():
    """Explicit token arg wins over MCPAAS_TOKEN env var."""
    with patch.dict(os.environ, {"MCPAAS_TOKEN": "env-value"}):
        mem = FAFMemory("grok", token="explicit-value")
        assert mem._token == "explicit-value"


def test_token_none_when_no_arg_or_env():
    """No token arg + no env = None (read still works for public souls)."""
    with patch.dict(os.environ, {}, clear=True):
        mem = FAFMemory("grok")
        assert mem._token is None


def test_scratchpad_composed():
    """FAFMemory composes a fresh Scratchpad."""
    mem = FAFMemory("grok")
    assert isinstance(mem.scratchpad, Scratchpad)
    assert len(mem.scratchpad) == 0


def test_tools_returns_two_callables():
    """tools() returns the etch + recall function-tool pair."""
    mem = FAFMemory("grok")
    tools = mem.tools()
    assert len(tools) == 2


@pytest.mark.network
async def test_etch_get_round_trip():
    """Live MCPaaS round-trip: write a tagged note, read it back.

    Writes to the `grok` dev soul (per `grok-soul-default-dev-target`
    convention). Token from MCPAAS_TOKEN env or the canonical dev
    token if env is unset.
    """
    token = os.environ.get("MCPAAS_TOKEN", "wolfe-68-orange")
    mem = FAFMemory("grok", token=token)

    timestamp = datetime.now(timezone.utc).isoformat()
    marker = f"pytest-roundtrip-{timestamp}"

    write_response = await mem.etch(
        marker,
        type="note",
        tags=["test", "pytest", "gate-2"],
    )
    assert "grok" in write_response
    assert marker in write_response

    soul_text = await mem.get()
    assert marker in soul_text


async def test_etch_payload_shape_mocked():
    """etch() builds the correct write_soul payload (mocked client)."""
    mem = FAFMemory("grok", token="test-token")

    captured: dict = {}

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "Note added to grok: hello"})()]

    class FakeClient:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            captured["tool_name"] = name
            captured["args"] = args
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        result = await mem.etch("hello", type="note", tags=["a", "b"])

    assert captured["tool_name"] == "write_soul"
    assert captured["args"]["soul"] == "grok"
    assert captured["args"]["entry"] == "hello"
    assert captured["args"]["type"] == "note"
    assert captured["args"]["tags"] == ["a", "b"]
    assert captured["args"]["token"] == "test-token"
    assert "Note added" in result


async def test_get_payload_shape_mocked():
    """get() builds the correct get_soul payload (mocked client)."""
    mem = FAFMemory("grok")

    captured: dict = {}

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "soul body here"})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            captured["tool_name"] = name
            captured["args"] = args
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        result = await mem.get()

    assert captured["tool_name"] == "get_soul"
    assert captured["args"] == {"soul": "grok"}
    assert result == "soul body here"


async def test_etch_raises_auth_error_when_no_token():
    """Without a token, etch() raises FAFAuthRequiredError with the
    friendly 'coming soon' guidance. Does not attempt to write.
    """
    from grok_faf_voice import FAFAuthRequiredError

    with patch.dict(os.environ, {}, clear=True):
        mem = FAFMemory("grok")

        # If the Client gets called, the test fails — the auth check
        # must happen BEFORE any network attempt.
        class TripwireClient:
            def __init__(self, url):
                raise AssertionError(
                    "Client should not be constructed when no token"
                )

        with patch("grok_faf_voice.memory.Client", TripwireClient):
            with pytest.raises(FAFAuthRequiredError) as exc_info:
                await mem.etch("hello")

        # The error message must guide the dev to the Voice key flow.
        assert "Voice key" in str(exc_info.value)


async def test_etch_tool_returns_friendly_message_when_no_token():
    """The @function_tool wrapper should catch FAFAuthRequiredError
    and return the friendly text — never a Python traceback to the
    realtime model.
    """
    from grok_faf_voice.tools import make_etch_tool

    with patch.dict(os.environ, {}, clear=True):
        mem = FAFMemory("grok")
        tool = make_etch_tool(mem)

        # FunctionTool wraps the original; we invoke the underlying
        # callable directly via .__wrapped__ if exposed, else via .info.
        callable_fn = getattr(tool, "__wrapped__", None) or tool.info.callable
        result = await callable_fn(None, "hello")

        assert "Voice key" in result


async def test_etch_wraps_invalid_soul_tool_error():
    """ToolError 'Invalid soul' from the server is wrapped in
    FAFEtchError with a friendly message naming the soul.
    """
    from fastmcp.exceptions import ToolError

    from grok_faf_voice import FAFEtchError

    mem = FAFMemory("madeup-soul-xyz", token="test-token")

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            raise ToolError(
                "Invalid soul. Valid: faf, grok, nelly, spacex, wolfe, "
                "liverpool, smithsonian"
            )

    with patch("grok_faf_voice.memory.Client", FakeClient):
        with pytest.raises(FAFEtchError) as exc_info:
            await mem.etch("hello")

    assert "madeup-soul-xyz" in str(exc_info.value)
    assert "Invalid soul" in str(exc_info.value)
    # Original ToolError chained for debug
    assert exc_info.value.__cause__ is not None


async def test_etch_wraps_generic_tool_error():
    """A non-recognized ToolError is still wrapped as FAFEtchError."""
    from fastmcp.exceptions import ToolError

    from grok_faf_voice import FAFEtchError

    mem = FAFMemory("grok", token="test-token")

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            raise ToolError("Some unrelated server error")

    with patch("grok_faf_voice.memory.Client", FakeClient):
        with pytest.raises(FAFEtchError) as exc_info:
            await mem.etch("hello")

    assert "Etch failed" in str(exc_info.value)
    assert "Some unrelated server error" in str(exc_info.value)


async def test_get_wraps_tool_error_as_recall_error():
    """ToolError on get_soul is wrapped as FAFRecallError, not FAFEtchError."""
    from fastmcp.exceptions import ToolError

    from grok_faf_voice import FAFRecallError

    mem = FAFMemory("grok")

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            raise ToolError("Some server-side recall error")

    with patch("grok_faf_voice.memory.Client", FakeClient):
        with pytest.raises(FAFRecallError) as exc_info:
            await mem.get()

    assert "Recall failed" in str(exc_info.value)
