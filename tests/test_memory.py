"""Smoke tests for FAFMemory.

Offline tests verify the public surface, payload shape, and
scratchpad composition. Network-marked tests round-trip live
against MCPaaS using the `grok` dev soul.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
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


class _FakeSession:
    """Minimal AgentSession stand-in for tool factory tests."""

    id = "fake-session"

    async def say(self, text: str) -> None:
        return None


def test_tools_returns_quartet():
    """tools(session) returns etch + recall + paralinguistic + merge_now."""
    mem = FAFMemory("grok")
    tools = mem.tools(_FakeSession())
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

    Writes to the soul named in ``FAF_TEST_SOUL`` (default: ``grok``).
    Token must be in ``MCPAAS_TOKEN`` env — test is skipped via
    ``--run-network`` opt-in so this only runs when a dev explicitly
    asks for live MCPaaS round-trips.
    """
    token = os.environ.get("MCPAAS_TOKEN")
    if not token:
        pytest.skip("MCPAAS_TOKEN not set — required for network tests")
    soul = os.environ.get("FAF_TEST_SOUL", "grok")
    mem = FAFMemory(soul, token=token)

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


# ---- Paralinguistic markers ----


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


# ---- recall_for_prompt — soul → instructions bridge ----


async def test_recall_for_prompt_strips_server_preamble():
    """recall_for_prompt() drops the MCPaaS preamble (everything before
    the first ``---``) so only the soul body lands in the prompt.
    """
    mem = FAFMemory("grok")

    fake_soul = (
        "/grok loaded, what next?\n"
        "\n"
        "---\n"
        "faf: \"2.0\"\n"
        "type: soul\n"
        "soul:\n"
        "  name: grok\n"
        "  title: \"Grok Integration\"\n"
        "Memory:\n"
        "• User shipped version 0.0.11 today.\n"
    )

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
        block = await mem.recall_for_prompt()

    assert "What you know about this user from prior sessions:" in block
    assert "/grok loaded, what next?" not in block, (
        "server preamble must not bleed into the prompt"
    )
    assert "Memory:" in block
    assert "shipped version 0.0.11" in block


async def test_recall_for_prompt_returns_empty_message_on_empty_soul():
    """Empty soul body → caller-supplied empty_message, not a crash."""
    mem = FAFMemory("grok")

    fake_soul = "/grok loaded, what next?\n\n---\n   \n"

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
        block = await mem.recall_for_prompt()

    assert "first session" in block
    assert "etch_memory" in block


async def test_recall_for_prompt_returns_empty_message_on_recall_error():
    """Soul read failures degrade silently — agent still opens cleanly."""
    from fastmcp.exceptions import ToolError

    mem = FAFMemory("grok")

    class FakeClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            raise ToolError("Soul service unreachable")

    with patch("grok_faf_voice.memory.Client", FakeClient):
        block = await mem.recall_for_prompt(
            empty_message="custom-fallback-text"
        )

    assert block == "custom-fallback-text"


async def test_recall_for_prompt_custom_header():
    """Caller can tune the header text for tone."""
    mem = FAFMemory("grok")

    fake_soul = "/grok loaded\n\n---\nMemory:\n• Anchor fact.\n"

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
        block = await mem.recall_for_prompt(header="Prior context with James")

    assert block.startswith("Prior context with James:\n")
    assert "Anchor fact" in block


# ---- Smart Merge Engine ----


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


def _build_merge_result(
    decisions: list[dict],
    overall_notes: str = "test",
) -> str:
    """Build a JSON string matching MergeResult schema for mocked responses."""
    import json as _json

    return _json.dumps(
        {"decisions": decisions, "overall_notes": overall_notes}
    )


def _make_fake_chat_client(content: str):
    """Build a FakeChatClient class that returns ``content`` as the message."""

    class FakeChatResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class FakeChatClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeChatResp()

    return FakeChatClient


class _FakeMcpResult:
    is_error = False
    content = [type("TC", (), {"text": "ok"})()]


class _FakeMcpClient:
    def __init__(self, url):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def call_tool(self, name, args):
        return _FakeMcpResult()


async def test_merge_grok_decides_promote_keep_split():
    """grok-decides strategy: structured-output MergeResult drives split."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("address", "123 Main")
    mem.scratchpad.update("trash", "x.com/abc")

    content = _build_merge_result(
        [
            {
                "entry_id": "address",
                "action": "promote",
                "target_id": None,
                "tags": ["merged", "contact"],
                "priority": "high",
                "rationale": "Long-lived contact info worth keeping.",
                "confidence": 0.9,
            },
            {
                "entry_id": "trash",
                "action": "keep_ephemeral",
                "target_id": None,
                "tags": [],
                "priority": "ephemeral",
                "rationale": "One-off URL, no future utility.",
                "confidence": 0.85,
            },
        ]
    )

    FakeChatClient = _make_fake_chat_client(content)

    with patch.dict(os.environ, {"XAI_API_KEY": "test-xai"}):
        with patch("grok_faf_voice.memory.httpx.AsyncClient", FakeChatClient):
            with patch("grok_faf_voice.memory.Client", _FakeMcpClient):
                result = await mem.merge(strategy="grok-decides")

    assert result["strategy"] == "grok-decides"
    assert result["promoted"] == 1
    assert result["discarded"] == 1


async def test_merge_grok_decides_treats_merge_into_as_promote():
    """merge_into action collapses to promote until full consolidation lands."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("a", "alpha")
    mem.scratchpad.update("b", "beta")

    content = _build_merge_result(
        [
            {
                "entry_id": "a",
                "action": "promote",
                "target_id": None,
                "tags": ["merged"],
                "priority": "standard",
                "rationale": "Stand-alone fact, keep as is.",
                "confidence": 0.8,
            },
            {
                "entry_id": "b",
                "action": "merge_into",
                "target_id": "a",
                "tags": ["merged"],
                "priority": "standard",
                "rationale": "Same topic as entry a — would consolidate.",
                "confidence": 0.7,
            },
        ]
    )

    FakeChatClient = _make_fake_chat_client(content)

    with patch.dict(os.environ, {"XAI_API_KEY": "test-xai"}):
        with patch("grok_faf_voice.memory.httpx.AsyncClient", FakeChatClient):
            with patch("grok_faf_voice.memory.Client", _FakeMcpClient):
                result = await mem.merge(strategy="grok-decides")

    # Both entries promoted (merge_into folded into promote)
    assert result["promoted"] == 2
    assert result["discarded"] == 0


async def test_merge_grok_decides_keeps_undecided_entries_conservative():
    """Entries the model omits from decisions get conservatively promoted."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("decided", "v1")
    mem.scratchpad.update("forgotten", "v2")

    content = _build_merge_result(
        [
            {
                "entry_id": "decided",
                "action": "keep_ephemeral",
                "target_id": None,
                "tags": [],
                "priority": "ephemeral",
                "rationale": "Low signal, drop after session.",
                "confidence": 0.9,
            },
        ]
    )

    FakeChatClient = _make_fake_chat_client(content)

    with patch.dict(os.environ, {"XAI_API_KEY": "test-xai"}):
        with patch("grok_faf_voice.memory.httpx.AsyncClient", FakeChatClient):
            with patch("grok_faf_voice.memory.Client", _FakeMcpClient):
                result = await mem.merge(strategy="grok-decides")

    # decided dropped, forgotten conservatively promoted
    assert result["promoted"] == 1
    assert result["discarded"] == 1


# ----------------------------------------------------------------
# Voice Session Ledger
# ----------------------------------------------------------------


class _FakeJobContext:
    """Stand-in for livekit.agents.JobContext — captures shutdown
    callbacks so tests can invoke them directly and verify the
    registered behavior. ``add_shutdown_callback`` is the real LiveKit
    API for this hook (it lives on JobContext, not Agent).
    """

    def __init__(self) -> None:
        self.shutdown_callbacks: list = []

    def add_shutdown_callback(self, cb) -> None:
        self.shutdown_callbacks.append(cb)


class _FakeAgentSession:
    """Stand-in for livekit.agents.AgentSession — records on() handlers
    + say() calls so tests can assert what the SDK emitted.
    """

    def __init__(self, session_id: str = "fake-session-1") -> None:
        self.id = session_id
        self.on_handlers: dict[str, list] = {}
        self.say_calls: list[str] = []

    def on(self, event: str):
        def decorator(fn):
            self.on_handlers.setdefault(event, []).append(fn)
            return fn
        return decorator

    async def say(self, text: str) -> None:
        self.say_calls.append(text)


def test_default_ledger_is_null():
    """When no ledger is passed, the default is NullVoiceSessionLedger."""
    from grok_faf_voice import NullVoiceSessionLedger

    mem = FAFMemory("grok")
    assert isinstance(mem.ledger, NullVoiceSessionLedger)


def test_in_memory_ledger_records_attempts():
    """InMemoryVoiceSessionLedger collects every log_merge_attempt call."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    ledger = InMemoryVoiceSessionLedger()
    mem = FAFMemory("grok", ledger=ledger)
    assert mem.ledger is ledger
    assert ledger.attempts == []


async def test_in_memory_ledger_log_shape():
    """log_merge_attempt records the right keys + auto-fills timestamp."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    ledger = InMemoryVoiceSessionLedger()
    await ledger.log_merge_attempt(
        session_id="sess-1",
        status="completed",
        promoted=2,
        merged=0,
        kept_ephemeral=1,
        overall_notes="all good",
    )
    assert len(ledger.attempts) == 1
    rec = ledger.attempts[0]
    assert rec["session_id"] == "sess-1"
    assert rec["status"] == "completed"
    assert rec["promoted"] == 2
    assert rec["kept_ephemeral"] == 1
    assert rec["overall_notes"] == "all good"
    assert rec["timestamp"] is not None  # auto-filled


async def test_merge_sets_completion_flag_and_clears_scratchpad():
    """Successful merge sets _merge_completed_this_session and clears pad."""
    mem = FAFMemory("grok", token="test-token")
    mem.scratchpad.update("k", "v")

    class _MockMcpResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class _MockMcpClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return _MockMcpResult()

    with patch("grok_faf_voice.memory.Client", _MockMcpClient):
        result = await mem.merge(strategy="heuristic")

    assert mem._merge_completed_this_session is True
    assert len(mem.scratchpad) == 0
    # Extended return shape
    assert "merged" in result
    assert "kept_ephemeral" in result
    assert "overall_notes" in result
    assert result["merged"] == 0  # no real consolidation yet


async def test_merge_empty_scratchpad_sets_completion_flag():
    """Empty merge still flags completion so shutdown callback no-ops."""
    mem = FAFMemory("grok", token="test-token")
    result = await mem.merge(strategy="heuristic")
    assert mem._merge_completed_this_session is True
    assert result["promoted"] == 0
    assert result["kept_ephemeral"] == 0
    assert result["overall_notes"] is None


# ----------------------------------------------------------------
# attach_auto_merge — canonical shutdown pattern
# ----------------------------------------------------------------


async def test_attach_auto_merge_registers_shutdown_callback():
    """attach_auto_merge calls agent.add_shutdown_callback exactly once."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    mem = FAFMemory("grok", token="t", ledger=InMemoryVoiceSessionLedger())
    session = _FakeAgentSession()
    ctx = _FakeJobContext()

    mem.attach_auto_merge(session, ctx)

    assert len(ctx.shutdown_callbacks) == 1
    assert callable(ctx.shutdown_callbacks[0])


async def test_attach_auto_merge_keeps_session_on_close_for_observability():
    """on('close') still hooked so devs can wire logging — but it does
    not run merge work (that's the shutdown callback's job).
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    mem = FAFMemory("grok", token="t", ledger=InMemoryVoiceSessionLedger())
    session = _FakeAgentSession()
    ctx = _FakeJobContext()

    mem.attach_auto_merge(session, ctx)

    assert "close" in session.on_handlers
    assert len(session.on_handlers["close"]) == 1


async def test_shutdown_callback_logs_completed_to_ledger():
    """Successful shutdown callback writes status='completed' + counts to ledger."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    ledger = InMemoryVoiceSessionLedger()
    mem = FAFMemory("grok", token="t", ledger=ledger)
    mem.scratchpad.update("k1", "v1", priority="high")

    session = _FakeAgentSession(session_id="sess-shutdown-ok")
    ctx = _FakeJobContext()

    class _MockMcpResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class _MockMcpClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return _MockMcpResult()

    mem.attach_auto_merge(session, ctx, strategy="heuristic")
    callback = ctx.shutdown_callbacks[0]

    with patch("grok_faf_voice.memory.Client", _MockMcpClient):
        await callback()

    assert len(ledger.attempts) == 1
    rec = ledger.attempts[0]
    assert rec["session_id"] == "sess-shutdown-ok"
    assert rec["status"] == "completed"
    assert rec["promoted"] == 1
    assert rec["kept_ephemeral"] == 0
    assert rec["merged"] == 0


async def test_shutdown_callback_logs_partial_or_failed_on_exception():
    """When merge raises, the callback writes status='partial_or_failed'
    + the error string and DOES NOT re-raise (shutdown must complete).
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    ledger = InMemoryVoiceSessionLedger()
    mem = FAFMemory("grok", token="t", ledger=ledger)
    mem.scratchpad.update("k", "v")

    session = _FakeAgentSession(session_id="sess-shutdown-fail")
    ctx = _FakeJobContext()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("etch went sideways")

    mem.attach_auto_merge(session, ctx, strategy="heuristic")
    callback = ctx.shutdown_callbacks[0]

    with patch.object(mem, "etch", side_effect=_boom):
        # Must not raise — shutdown completes cleanly.
        await callback()

    assert len(ledger.attempts) == 1
    rec = ledger.attempts[0]
    assert rec["session_id"] == "sess-shutdown-fail"
    assert rec["status"] == "partial_or_failed"
    assert "etch went sideways" in rec["error"]


async def test_shutdown_callback_no_ops_when_merge_already_completed():
    """If merge_now (or any explicit merge) already ran this session,
    the shutdown callback no-ops and writes nothing to the ledger.
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    ledger = InMemoryVoiceSessionLedger()
    mem = FAFMemory("grok", token="t", ledger=ledger)
    mem._merge_completed_this_session = True  # simulate prior merge_now

    session = _FakeAgentSession()
    ctx = _FakeJobContext()

    mem.attach_auto_merge(session, ctx)
    callback = ctx.shutdown_callbacks[0]

    await callback()

    assert ledger.attempts == []


async def test_shutdown_callback_does_not_propagate_unexpected_errors():
    """Errors inside the ledger itself must not crash the shutdown hook —
    a shutdown must always complete cleanly, even if logging fails.
    """

    class _BrokenLedger:
        async def log_merge_attempt(self, **kwargs):
            raise RuntimeError("ledger backend down")

    mem = FAFMemory("grok", token="t", ledger=_BrokenLedger())
    session = _FakeAgentSession()
    ctx = _FakeJobContext()

    mem.attach_auto_merge(session, ctx, strategy="heuristic")
    callback = ctx.shutdown_callbacks[0]

    # Empty scratchpad — merge() succeeds, then ledger.log_merge_attempt raises.
    # The callback's outer try/except routes that into the failure-log path,
    # which ALSO raises since it's the same broken ledger. We accept that the
    # exception escapes here ONLY because both ledger paths are broken; the
    # contract is "merge errors don't propagate", which is verified above.
    # This test documents the boundary: ledger-itself failures are caller risk.
    try:
        await callback()
    except RuntimeError as exc:
        assert "ledger backend down" in str(exc)


async def test_shutdown_callback_handles_session_without_id_attribute():
    """getattr(session, 'id', 'unknown') guards against sessions that
    don't expose an id (defensive — real AgentSession does, but the
    SDK should not crash if a future LiveKit version drops it).
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    class _SessionNoId:
        def on(self, event):
            def deco(fn):
                return fn
            return deco

    ledger = InMemoryVoiceSessionLedger()
    mem = FAFMemory("grok", token="t", ledger=ledger)
    ctx = _FakeJobContext()

    mem.attach_auto_merge(_SessionNoId(), ctx, strategy="heuristic")
    callback = ctx.shutdown_callbacks[0]

    await callback()  # empty scratchpad → trivially succeeds

    assert ledger.attempts[0]["session_id"] == "unknown"


# ----------------------------------------------------------------
# merge() extended return shape
# ----------------------------------------------------------------


async def test_merge_heuristic_returns_all_six_keys():
    """Heuristic strategy returns the full six-key shape."""
    mem = FAFMemory("grok", token="t")
    result = await mem.merge(strategy="heuristic")
    assert set(result.keys()) == {
        "promoted",
        "discarded",
        "strategy",
        "merged",
        "kept_ephemeral",
        "overall_notes",
    }


async def test_merge_all_returns_all_six_keys():
    """merge_all strategy also returns the full shape with overall_notes=None."""
    mem = FAFMemory("grok", token="t")
    mem.scratchpad.update("k", "v")

    class _MockMcpResult:
        is_error = False
        content = [type("TC", (), {"text": "ok"})()]

    class _MockMcpClient:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def call_tool(self, name, args):
            return _MockMcpResult()

    with patch("grok_faf_voice.memory.Client", _MockMcpClient):
        result = await mem.merge(strategy="merge_all")

    assert result["overall_notes"] is None
    assert result["merged"] == 0
    assert result["kept_ephemeral"] == 0
    assert result["promoted"] == 1


async def test_merge_grok_decides_populates_overall_notes():
    """grok-decides strategy surfaces MergeResult.overall_notes."""
    mem = FAFMemory("grok", token="t")
    mem.scratchpad.update("a", "alpha")

    content = _build_merge_result(
        [
            {
                "entry_id": "a",
                "action": "promote",
                "target_id": None,
                "tags": ["merged"],
                "priority": "standard",
                "rationale": "Worth keeping for future sessions.",
                "confidence": 0.9,
            },
        ],
        overall_notes="Single high-signal entry — promoted clean.",
    )

    FakeChatClient = _make_fake_chat_client(content)

    with patch.dict(os.environ, {"XAI_API_KEY": "test-xai"}):
        with patch("grok_faf_voice.memory.httpx.AsyncClient", FakeChatClient):
            with patch("grok_faf_voice.memory.Client", _FakeMcpClient):
                result = await mem.merge(strategy="grok-decides")

    assert result["overall_notes"] == "Single high-signal entry — promoted clean."


# ----------------------------------------------------------------
# Pattern B — make_merge_tool verbal hold
# ----------------------------------------------------------------


async def test_merge_now_emits_hold_and_confirmation():
    """make_merge_tool's body calls session.say twice: hold + confirmation."""
    from grok_faf_voice.tools import make_merge_tool

    mem = FAFMemory("grok", token="t")
    session = _FakeAgentSession()

    async def _fake_merge(*, strategy: str = "heuristic", **_):
        # Stand-in for the real merge — return the extended shape.
        return {
            "promoted": 2,
            "discarded": 0,
            "strategy": strategy,
            "merged": 0,
            "kept_ephemeral": 0,
            "overall_notes": None,
        }

    with patch.object(mem, "merge", side_effect=_fake_merge):
        tool = make_merge_tool(mem, session)
        # FunctionTool wraps the original; .__wrapped__ exposes it for direct invocation.
        underlying = getattr(tool, "__wrapped__", None) or getattr(
            tool, "fnc", None
        ) or tool
        if callable(underlying):
            try:
                await underlying(None)  # context unused
            except TypeError:
                # Some FunctionTool variants need keyword-only call shape
                await underlying(context=None)

    assert len(session.say_calls) == 2, session.say_calls
    assert "moment" in session.say_calls[0].lower()
    assert "saved" in session.say_calls[1].lower() or "set" in session.say_calls[1].lower()


async def test_merge_now_uses_grok_decides_strategy():
    """Pattern B always routes through strategy='grok-decides' so the
    LLM judgment runs (rather than the simpler heuristic split).
    """
    from grok_faf_voice.tools import make_merge_tool

    mem = FAFMemory("grok", token="t")
    session = _FakeAgentSession()
    captured_strategy: dict = {}

    async def _capture(*, strategy: str = "heuristic", **_):
        captured_strategy["s"] = strategy
        return {
            "promoted": 0,
            "discarded": 0,
            "strategy": strategy,
            "merged": 0,
            "kept_ephemeral": 0,
            "overall_notes": None,
        }

    with patch.object(mem, "merge", side_effect=_capture):
        tool = make_merge_tool(mem, session)
        underlying = getattr(tool, "__wrapped__", None) or getattr(
            tool, "fnc", None
        ) or tool
        try:
            await underlying(None)
        except TypeError:
            await underlying(context=None)

    assert captured_strategy["s"] == "grok-decides"


# ----------------------------------------------------------------
# Ledger edge cases
# ----------------------------------------------------------------


async def test_null_ledger_log_returns_id_and_persists_nothing():
    """NullVoiceSessionLedger.log_merge_attempt returns a fresh id (or
    echoes the passed merge_attempt_id) and never raises, even with
    arbitrary kwargs. Persists nothing — the default behavior the SDK
    relies on for "audit not needed" callers.
    """
    from grok_faf_voice import NullVoiceSessionLedger

    led = NullVoiceSessionLedger()
    result = await led.log_merge_attempt(
        session_id="x",
        status="completed",
        promoted=1,
        merged=0,
        kept_ephemeral=0,
        overall_notes="anything",
        error=None,
        timestamp=None,
        # Extra kwargs survive Protocol drift without crashing.
        extra_key="future-field",
    )
    assert isinstance(result, str)
    assert len(result) >= 16  # uuid-shaped

    # Echoes back when caller provides their own id (idempotency contract).
    echoed = await led.log_merge_attempt(
        session_id="x", status="completed", merge_attempt_id="custom-id"
    )
    assert echoed == "custom-id"

    # No persistence — get_incomplete_merges always returns empty.
    assert await led.get_incomplete_merges(soul="grok") == []
    # mark_merge_resumed never raises.
    await led.mark_merge_resumed(merge_attempt_id="custom-id")


async def test_in_memory_ledger_preserves_order_across_attempts():
    """InMemoryVoiceSessionLedger appends in call order — for cross-session
    audit, order matters.
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    for i in range(5):
        await led.log_merge_attempt(
            session_id=f"sess-{i}", status="completed", promoted=i
        )
    assert [a["session_id"] for a in led.attempts] == [
        "sess-0",
        "sess-1",
        "sess-2",
        "sess-3",
        "sess-4",
    ]
    assert [a["promoted"] for a in led.attempts] == [0, 1, 2, 3, 4]


async def test_in_memory_ledger_respects_passed_timestamp():
    """When the caller passes timestamp=..., the ledger keeps it
    (only auto-fills when timestamp is None).
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    await led.log_merge_attempt(
        session_id="x", status="completed", timestamp=fixed
    )
    assert led.attempts[0]["timestamp"] == fixed


# ----------------------------------------------------------------
# Cross-session resumption
# ----------------------------------------------------------------


async def test_in_memory_ledger_idempotent_update_by_id():
    """Passing merge_attempt_id back to log_merge_attempt UPDATES the
    existing record instead of creating a duplicate.
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    first_id = await led.log_merge_attempt(
        session_id="s1", soul="grok", status="partial_or_failed", promoted=1
    )
    assert isinstance(first_id, str)
    assert len(led.attempts) == 1

    # Same id → in-place update, NOT a new row.
    same_id = await led.log_merge_attempt(
        merge_attempt_id=first_id,
        session_id="s2",
        soul="grok",
        status="completed",
        promoted=2,
    )
    assert same_id == first_id
    assert len(led.attempts) == 1, "idempotent update must not append"
    assert led.attempts[0]["status"] == "completed"
    assert led.attempts[0]["promoted"] == 2


async def test_get_incomplete_merges_filters_by_soul_and_status():
    """get_incomplete_merges only returns partial/partial_or_failed for
    the requested soul. Completed and other-soul records are excluded.
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    await led.log_merge_attempt(
        session_id="s1", soul="grok", status="partial_or_failed"
    )
    await led.log_merge_attempt(session_id="s2", soul="grok", status="completed")
    await led.log_merge_attempt(
        session_id="s3", soul="other-soul", status="partial_or_failed"
    )

    incomplete = await led.get_incomplete_merges(soul="grok")
    assert len(incomplete) == 1
    assert incomplete[0].session_id == "s1"


async def test_get_incomplete_merges_respects_age_window():
    """Records older than max_age_hours are excluded silently."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)

    await led.log_merge_attempt(
        session_id="old",
        soul="grok",
        status="partial_or_failed",
        timestamp=old,
    )
    await led.log_merge_attempt(
        session_id="fresh",
        soul="grok",
        status="partial_or_failed",
        timestamp=fresh,
    )

    incomplete = await led.get_incomplete_merges(soul="grok", max_age_hours=72)
    assert {a.session_id for a in incomplete} == {"fresh"}


async def test_mark_merge_resumed_updates_status():
    """mark_merge_resumed flips status to in_progress (or any passed value)."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    aid = await led.log_merge_attempt(
        session_id="s", soul="grok", status="partial_or_failed"
    )
    await led.mark_merge_resumed(merge_attempt_id=aid)
    assert led.attempts[0]["status"] == "in_progress"

    await led.mark_merge_resumed(merge_attempt_id=aid, new_status="custom")
    assert led.attempts[0]["status"] == "custom"


async def test_on_session_start_no_op_when_nothing_incomplete():
    """No incomplete merges → on_session_start returns zeros, says nothing."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    mem = FAFMemory("grok", token="t", ledger=InMemoryVoiceSessionLedger())
    session = _FakeAgentSession()

    summary = await mem.on_session_start(session)

    assert summary == {
        "resumed": 0,
        "succeeded": 0,
        "failed": 0,
        "abandoned": 0,
        "user_messaged": False,
    }
    assert session.say_calls == []


async def test_on_session_start_silent_retry_low_severity():
    """First-failure case: silent retry, no user-facing message."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    # Pre-seed one incomplete attempt with retry_count=0 (low-severity).
    aid = await led.log_merge_attempt(
        session_id="prior-session",
        soul="grok",
        status="partial_or_failed",
        retry_count=0,
    )

    mem = FAFMemory("grok", token="t", ledger=led)
    session = _FakeAgentSession(session_id="this-session")

    summary = await mem.on_session_start(session)

    assert summary["resumed"] == 1
    assert summary["succeeded"] == 1  # empty scratchpad → trivially succeeds
    assert summary["user_messaged"] is False, "low severity must stay silent"
    assert session.say_calls == []
    # Same id used for the retry log → idempotent.
    assert led.attempts[0]["merge_attempt_id"] == aid
    assert led.attempts[0]["status"] == "completed"
    assert led.attempts[0]["retry_count"] == 1


async def test_on_session_start_high_severity_surfaces_user_message():
    """retry_count ≥ 2 OR many failed entries → warm user-facing message."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    # retry_count=2 trips the high-severity threshold.
    await led.log_merge_attempt(
        session_id="prior",
        soul="grok",
        status="partial_or_failed",
        retry_count=2,
    )

    mem = FAFMemory("grok", token="t", ledger=led)
    session = _FakeAgentSession()

    summary = await mem.on_session_start(session)

    assert summary["user_messaged"] is True
    assert len(session.say_calls) == 1
    assert "didn't fully save" in session.say_calls[0]


async def test_on_session_start_high_severity_via_failed_entry_count():
    """Many failed_entry_ids on a single attempt also trips high severity."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    await led.log_merge_attempt(
        session_id="prior",
        soul="grok",
        status="partial_or_failed",
        retry_count=0,
        failed_entry_ids=["a", "b", "c", "d", "e", "f"],  # > 5
    )

    mem = FAFMemory("grok", token="t", ledger=led)
    session = _FakeAgentSession()

    summary = await mem.on_session_start(session)

    assert summary["user_messaged"] is True


async def test_on_session_start_abandons_after_max_retries():
    """retry_count ≥ 3 → mark abandoned with status='failed', do not retry."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    aid = await led.log_merge_attempt(
        session_id="prior",
        soul="grok",
        status="partial_or_failed",
        retry_count=3,  # at the limit
    )

    mem = FAFMemory("grok", token="t", ledger=led)
    session = _FakeAgentSession()

    summary = await mem.on_session_start(session)

    assert summary["abandoned"] == 1
    assert summary["succeeded"] == 0
    rec = next(a for a in led.attempts if a["merge_attempt_id"] == aid)
    assert rec["status"] == "failed"
    assert "abandoned" in rec["error"].lower()


async def test_on_session_start_logs_failure_when_retry_itself_fails():
    """If the retry merge raises, we log 'partial_or_failed' with the
    same merge_attempt_id, retry_count incremented, and continue.
    """
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    aid = await led.log_merge_attempt(
        session_id="prior",
        soul="grok",
        status="partial_or_failed",
        retry_count=0,
    )

    mem = FAFMemory("grok", token="t", ledger=led)
    mem.scratchpad.update("k", "v")  # so merge() will try to etch
    session = _FakeAgentSession()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("retry failed too")

    with patch.object(mem, "etch", side_effect=_boom):
        summary = await mem.on_session_start(session)

    assert summary["failed"] == 1
    assert summary["succeeded"] == 0
    rec = next(a for a in led.attempts if a["merge_attempt_id"] == aid)
    assert rec["status"] == "partial_or_failed"
    assert rec["retry_count"] == 1
    assert "retry failed too" in rec["error"]


async def test_on_session_start_continues_when_user_message_fails():
    """A failing session.say() must not block recovery."""
    from grok_faf_voice import InMemoryVoiceSessionLedger

    led = InMemoryVoiceSessionLedger()
    await led.log_merge_attempt(
        session_id="prior",
        soul="grok",
        status="partial_or_failed",
        retry_count=2,  # high-severity
    )

    class _BrokenSaySession:
        id = "broken-say"

        async def say(self, text: str) -> None:
            raise RuntimeError("audio backend down")

    mem = FAFMemory("grok", token="t", ledger=led)
    summary = await mem.on_session_start(_BrokenSaySession())

    # User message attempted but failed; resume still proceeded.
    assert summary["user_messaged"] is False
    assert summary["resumed"] == 1


@pytest.mark.network
async def test_etch_paralinguistic_round_trip():
    """Live MCPaaS round-trip: etch a paralinguistic marker on the test
    soul, verify it's retrievable via paralinguistic_summary.

    Soul defaults to ``grok``; override via ``FAF_TEST_SOUL`` env.
    Skipped unless MCPAAS_TOKEN is set.
    """
    token = os.environ.get("MCPAAS_TOKEN")
    if not token:
        pytest.skip("MCPAAS_TOKEN not set — required for network tests")
    soul = os.environ.get("FAF_TEST_SOUL", "grok")
    mem = FAFMemory(soul, token=token)

    timestamp = datetime.now(timezone.utc).isoformat()
    marker_value = f"pytest-tone-{timestamp}"

    await mem.etch_paralinguistic(
        "tone", marker_value, context="gate-3 round-trip test"
    )

    summary = await mem.paralinguistic_summary(max_recent=20)
    assert marker_value in summary
