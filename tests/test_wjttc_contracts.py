"""WJTTC ENGINE-tier upstream contract pins.

Pin the surface shape of every fast-moving upstream we depend on
(LiveKit Agents, xAI plugin, xAI chat completions, MCPaaS, fastmcp,
Pydantic). When an upstream renames a symbol or changes a kwarg, one
of these tests goes red — well before any user hits it in production.

These do **not** make network calls. They probe import resolution,
attribute presence, signature shape, and source-string literals.
Live-network probes live in TYRES (`scripts/wjttc.sh --tyres`).

Why source-string literal pins for MCPaaS tool names? Because the
SDK calls them as `client.call_tool("write_soul", ...)` — bare
strings, not enum constants. If MCPaaS renames `write_soul` to
`save_soul`, the SDK breaks. The literal pin makes a rename in our
own code an intentional act, not a typo that slips through.
"""

from __future__ import annotations

import inspect

# ----------------------------------------------------------------
# LiveKit Agents — the orchestration layer
# ----------------------------------------------------------------


def test_livekit_imports_resolve():
    """Every LiveKit symbol referenced by the SDK must import cleanly."""
    from livekit.agents import (  # noqa: F401
        Agent,
        AgentServer,
        AgentSession,
        JobContext,
        RunContext,
        function_tool,
    )


def test_livekit_jobcontext_has_add_shutdown_callback():
    """`attach_auto_merge` registers a shutdown callback via this API.
    If it disappears, our reliable-shutdown story breaks."""
    from livekit.agents import JobContext

    assert hasattr(JobContext, "add_shutdown_callback"), (
        "JobContext.add_shutdown_callback missing — "
        "LiveKit may have renamed or moved it"
    )


def test_livekit_agentserver_has_rtc_session_decorator():
    """The README example registers entrypoints via `@server.rtc_session()`."""
    from livekit.agents import AgentServer

    server = AgentServer()
    assert hasattr(server, "rtc_session"), (
        "AgentServer.rtc_session missing — README example would break"
    )
    assert callable(server.rtc_session)


def test_livekit_function_tool_callable():
    """`@function_tool` decorates the four FAFMemory tools."""
    from livekit.agents import function_tool

    assert callable(function_tool)


# ----------------------------------------------------------------
# xAI plugin (livekit.plugins.xai) — the realtime voice model
# ----------------------------------------------------------------


def test_xai_realtime_model_importable():
    """`xai.realtime.RealtimeModel` is the realtime LLM the README uses."""
    from livekit.plugins import xai

    assert hasattr(xai, "realtime")
    assert hasattr(xai.realtime, "RealtimeModel")


def test_xai_realtime_model_accepts_voice_and_turn_detection():
    """README example passes `voice="Ara"` and a `turn_detection` dict.
    If either kwarg is renamed, the example breaks."""
    from livekit.plugins.xai.realtime import RealtimeModel

    sig = inspect.signature(RealtimeModel.__init__)
    params = sig.parameters
    assert "voice" in params, (
        "RealtimeModel no longer accepts `voice=` — "
        "README example would fail at construction"
    )
    assert "turn_detection" in params, (
        "RealtimeModel no longer accepts `turn_detection=` — "
        "README example would fail at construction"
    )


# ----------------------------------------------------------------
# xAI chat completions — the Smart Merge Engine path
# ----------------------------------------------------------------


def test_xai_chat_url_unchanged():
    """The merge engine POSTs to this exact URL. Pinned to catch a
    silent base-URL move."""
    from grok_faf_voice.memory import XAI_CHAT_URL

    assert XAI_CHAT_URL == "https://api.x.ai/v1/chat/completions"


def test_xai_structured_outputs_request_uses_strict_json_schema():
    """xAI strict-mode structured outputs require
    `response_format.type == "json_schema"` and `strict: true`. If we
    ever swap to `json_object`, parsing reliability drops — pin the
    literal so the change is intentional."""
    from grok_faf_voice.memory import FAFMemory

    src = inspect.getsource(FAFMemory._grok_decides_split)
    assert '"json_schema"' in src, (
        "grok-decides path no longer requests json_schema "
        "structured outputs — strict-mode parsing at risk"
    )
    assert '"strict": True' in src, (
        "grok-decides path no longer requests strict mode — "
        "schema validation may be skipped"
    )


def test_xai_default_chat_model_is_reasoning_variant():
    """Reasoning model is judgment-grade for memory consolidation.
    A regression to non-reasoning is a quality drop worth catching."""
    from grok_faf_voice.memory import XAI_CHAT_MODEL_DEFAULT

    assert "reasoning" in XAI_CHAT_MODEL_DEFAULT, (
        f"Default chat model {XAI_CHAT_MODEL_DEFAULT!r} is not a "
        "reasoning variant — Smart Merge quality regression"
    )


# ----------------------------------------------------------------
# MCPaaS — the persistence backend
# ----------------------------------------------------------------


def test_mcpaas_endpoint_constants_unchanged():
    """The two MCPaaS URLs the SDK targets. Pin the exact literals
    so an environment-config drift is caught at test time."""
    from grok_faf_voice.context import MCPAAS_RAW_URL
    from grok_faf_voice.memory import MCPAAS_URL

    assert MCPAAS_URL == "https://mcpaas.live/mcp"
    assert MCPAAS_RAW_URL == "https://mcpaas.live/raw/{slug}"


def test_mcpaas_tool_names_referenced_in_source():
    """MCPaaS exposes `write_soul` and `get_soul` as MCP tool names.
    The SDK calls them as bare strings — if MCPaaS renames either,
    the SDK breaks. Pin the literals so a rename in our code is an
    intentional act, not a typo."""
    from grok_faf_voice import memory as m

    src = inspect.getsource(m)
    assert '"write_soul"' in src, "MCPaaS write_soul tool name no longer referenced"
    assert '"get_soul"' in src, "MCPaaS get_soul tool name no longer referenced"


def test_mcpaas_api_key_env_var_name():
    """Env var name is the public SDK contract. Pin
    `MCPAAS_API_KEY` so an accidental rename back to the old
    `MCPAAS_TOKEN` (or any other variant) trips the test rather
    than silently breaking every user's `.env`."""
    from grok_faf_voice.memory import FAFMemory

    src = inspect.getsource(FAFMemory.__init__)
    assert '"MCPAAS_API_KEY"' in src, (
        "MCPAAS_API_KEY env var no longer read in __init__ — "
        ".env-based auth would silently break"
    )
    assert '"MCPAAS_TOKEN"' not in src, (
        "Old MCPAAS_TOKEN env var name resurfaced — should be "
        "MCPAAS_API_KEY (renamed in v0.1.2)"
    )


# ----------------------------------------------------------------
# fastmcp — the MCP client library
# ----------------------------------------------------------------


def test_fastmcp_client_has_call_tool():
    """The Client.call_tool method is how we invoke write_soul/get_soul."""
    from fastmcp import Client

    assert hasattr(Client, "call_tool"), (
        "fastmcp.Client.call_tool missing — MCPaaS calls would fail"
    )


def test_fastmcp_tool_error_class_exists():
    """ToolError is what we catch when MCPaaS returns a structured failure."""
    from fastmcp.exceptions import ToolError

    assert issubclass(ToolError, Exception)


# ----------------------------------------------------------------
# Pydantic / structured-output schema
# ----------------------------------------------------------------


def test_merge_result_schema_has_decisions_property():
    """xAI strict structured outputs require a fully-typed schema.
    `decisions` is the load-bearing field — if it disappears, the
    grok-decides merge path returns garbage."""
    from grok_faf_voice._merge_models import MergeResult

    schema = MergeResult.model_json_schema()
    assert "decisions" in schema.get("properties", {}), (
        "MergeResult schema lost `decisions` property — "
        "xAI structured-outputs response will be empty"
    )


def test_merge_decision_action_literal_unchanged():
    """The three actions the merge engine knows. A new value here
    silently breaks the dispatch in `_grok_decides_split`."""
    from grok_faf_voice._merge_models import MergeDecision

    schema = MergeDecision.model_json_schema()
    action_field = schema["properties"]["action"]
    enum_values = set(action_field.get("enum") or [])
    assert enum_values == {"promote", "keep_ephemeral", "merge_into"}, (
        f"MergeDecision.action enum drifted: got {enum_values}"
    )
