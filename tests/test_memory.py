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


def test_tools_returns_quartet():
    """tools() returns etch + recall + paralinguistic + merge_now."""
    mem = FAFMemory("grok")
    tools = mem.tools()
    assert len(tools) == 4
    names = {t.info.name for t in tools}
    assert names == {
        "etch_memory",
        "recall_memory",
        "note_paralinguistic",
        "merge_now",
    }


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


# ---- Gate 3 — Paralinguistic markers ----


async def test_etch_paralinguistic_payload_shape():
    """etch_paralinguistic() builds correct write_soul args:
    type='paralinguistic', tag list includes both 'paralinguistic' and
    the marker_type, entry has the canonical prefix.
    """
    mem = FAFMemory("grok", token="test-token")

    captured: dict = {}

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "Note added"})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            captured["args"] = args
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        await mem.etch_paralinguistic(
            "tone", "frustrated", context="checkout flow"
        )

    args = captured["args"]
    assert args["type"] == "paralinguistic"
    assert "paralinguistic" in args["tags"]
    assert "tone" in args["tags"]
    assert "[paralinguistic]" in args["entry"]
    assert "tone: frustrated" in args["entry"]
    assert "checkout flow" in args["entry"]


async def test_etch_paralinguistic_without_context():
    """etch_paralinguistic() works without the optional context arg."""
    mem = FAFMemory("grok", token="test-token")

    captured: dict = {}

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            captured["args"] = args
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        await mem.etch_paralinguistic("speaking_style", "rapid")

    entry = captured["args"]["entry"]
    assert "[paralinguistic]" in entry
    assert "speaking_style: rapid" in entry
    # No "—" trailing context fragment when context is omitted
    assert "—" not in entry


async def test_paralinguistic_summary_extracts_correctly():
    """paralinguistic_summary() pulls prefixed lines, strips bullets and
    trailing tag blocks, returns a one-line synopsis.
    """
    mem = FAFMemory("grok")

    fake_soul = """
type: soul
soul:
  name: grok
---
# Note:
• Some unrelated entry [test]

# Paralinguistic:
• [paralinguistic] tone: frustrated — checkout flow [paralinguistic, tone]
• [paralinguistic] speaking_style: rapid — under pressure [paralinguistic, speaking_style]

# Note:
• Another unrelated entry [demo]
"""

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": fake_soul})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        summary = await mem.paralinguistic_summary()

    assert "Prior voice context" in summary
    assert "tone: frustrated" in summary
    assert "speaking_style: rapid" in summary
    # Tag block should NOT appear in the summary
    assert "[paralinguistic, tone]" not in summary


async def test_paralinguistic_summary_empty_when_no_markers():
    """paralinguistic_summary() returns empty string when soul has no
    paralinguistic entries — caller can compose conditionally.
    """
    mem = FAFMemory("grok")

    fake_soul = """
type: soul
soul:
  name: grok
---
# Note:
• Just a regular note [demo]
"""

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": fake_soul})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        summary = await mem.paralinguistic_summary()

    assert summary == ""


# ---- Gate 4 — Smart Merge Engine ----


async def test_merge_empty_scratchpad_returns_zeros():
    """merge() on an empty scratchpad does no I/O and returns zeros."""
    mem = FAFMemory("grok", token="test-token")
    result = await mem.merge()
    assert result["promoted"] == 0
    assert result["discarded"] == 0


async def test_merge_heuristic_keeps_non_ephemeral():
    """Heuristic strategy: discard 'ephemeral' priority, keep others."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("address", "123 Main St", priority="high")
    mem.scratchpad.update("name", "James", priority="medium")
    mem.scratchpad.update("random_url", "x.com/abc", priority="ephemeral")

    captured_etches: list = []

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "Note added"})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            captured_etches.append(args)
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        result = await mem.merge()

    assert result["promoted"] == 2
    assert result["discarded"] == 1
    assert result["strategy"] == "heuristic"
    # Both promoted etches went out
    assert len(captured_etches) == 2
    # All carry the [merged] tag
    for args in captured_etches:
        assert "merged" in args["tags"]
    # Scratchpad cleared
    assert len(mem.scratchpad) == 0


async def test_merge_promotes_with_smart_tag_in_tags():
    """Entries with a tag get that tag in the etched tag list too."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("addr", "123 Main", tag="contact")

    captured: list = []

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            captured.append(args)
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        await mem.merge()

    assert "merged" in captured[0]["tags"]
    assert "contact" in captured[0]["tags"]


async def test_merge_all_promotes_ephemeral_too():
    """strategy='merge_all' bypasses the priority filter."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("trash", "x", priority="ephemeral")

    class FakeResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return FakeResult()

    with patch("grok_faf_voice.memory.Client", FakeClient):
        result = await mem.merge(strategy="merge_all")

    assert result["promoted"] == 1
    assert result["discarded"] == 0


async def test_merge_unknown_strategy_raises():
    """Bad strategy name raises ValueError immediately."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("k", "v")
    with pytest.raises(ValueError, match="Unknown merge strategy"):
        await mem.merge(strategy="garbage")


async def test_merge_grok_decides_parses_keep_discard():
    """grok-decides strategy: parse JSON decision and apply."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("address", "123 Main")
    mem.scratchpad.update("trash", "x.com/abc")

    grok_response = {
        "choices": [
            {
                "message": {
                    "content": '{"keep": ["address"], "discard": ["trash"]}'
                }
            }
        ]
    }

    class FakeChatResp:
        def raise_for_status(self):
            return None

        def json(self):
            return grok_response

    class FakeChatClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeChatResp()

    class FakeMcpResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class FakeMcpClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return FakeMcpResult()

    with patch.dict(os.environ, {"XAI_API_KEY": "test-xai"}):
        with patch("grok_faf_voice.memory.httpx.AsyncClient", FakeChatClient):
            with patch("grok_faf_voice.memory.Client", FakeMcpClient):
                result = await mem.merge(strategy="grok-decides")

    assert result["strategy"] == "grok-decides"
    assert result["promoted"] == 1
    assert result["discarded"] == 1


async def test_merge_grok_decides_falls_back_conservative_on_bad_json():
    """If Grok returns garbage, conservative fallback keeps everything."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("a", "1")
    mem.scratchpad.update("b", "2")

    grok_garbage = {"choices": [{"message": {"content": "not-json-at-all"}}]}

    class FakeChatResp:
        def raise_for_status(self):
            return None

        def json(self):
            return grok_garbage

    class FakeChatClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeChatResp()

    class FakeMcpResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class FakeMcpClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return FakeMcpResult()

    with patch.dict(os.environ, {"XAI_API_KEY": "test-xai"}):
        with patch("grok_faf_voice.memory.httpx.AsyncClient", FakeChatClient):
            with patch("grok_faf_voice.memory.Client", FakeMcpClient):
                result = await mem.merge(strategy="grok-decides")

    # Conservative: keep everything when parsing fails
    assert result["promoted"] == 2
    assert result["discarded"] == 0


@pytest.mark.network
async def test_etch_paralinguistic_round_trip():
    """Live MCPaaS round-trip: etch a paralinguistic marker on grok soul,
    verify it's retrievable via paralinguistic_summary.
    """
    token = os.environ.get("MCPAAS_TOKEN", "wolfe-68-orange")
    mem = FAFMemory("grok", token=token)

    timestamp = datetime.now(timezone.utc).isoformat()
    marker_value = f"pytest-tone-{timestamp}"

    await mem.etch_paralinguistic(
        "tone", marker_value, context="gate-3 round-trip test"
    )

    summary = await mem.paralinguistic_summary(max_recent=20)
    assert marker_value in summary
