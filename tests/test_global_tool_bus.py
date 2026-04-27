"""Tests for enable_global_tool_bus — full coverage over every tool.

Verifies that the global wrapper:
- Adds tool.about_to_run + tool.completed events to ANY tool
- Skips tools tagged _bus_wrapped (FAFMemory factory output)
- Is idempotent (multiple calls don't double-wrap)
- Preserves LiveKit FunctionTool metadata (name, description)
- Re-raises tool exceptions so LiveKit error handling works
- Patches agent.update_tools so future additions also get wrapped
"""

from __future__ import annotations

import asyncio

import pytest
from livekit.agents import function_tool

from grok_faf_voice import (
    BusEvent,
    BusEventPayload,
    FAFMemory,
    enable_global_tool_bus,
)


class _FakeAgent:
    """Stand-in for livekit.agents.Agent — only what the global wrapper
    touches: ``tools`` list and ``update_tools`` async method.
    """

    def __init__(self, tools: list | None = None) -> None:
        self.tools: list = list(tools or [])

    async def update_tools(self, tools: list) -> None:
        self.tools = list(tools)


def _make_user_tool(name: str = "user_tool", body=None):
    """Build a user-defined @function_tool the wrapper hasn't seen."""
    if body is None:
        async def _default():
            return f"{name} ran"
        body = _default

    body.__name__ = name

    @function_tool
    async def user_tool() -> str:
        return await body()

    return user_tool


# ----------------------------------------------------------------
# Wrapping
# ----------------------------------------------------------------


async def test_global_wrapper_emits_pre_and_post_events():
    """A user-defined tool fires tool.about_to_run + tool.completed
    when invoked through the global wrapper.
    """
    mem = FAFMemory("grok", token="t")
    await mem.start_bus()

    received: list[BusEventPayload] = []

    async def on_pre(payload: BusEventPayload) -> None:
        received.append(payload)

    async def on_post(payload: BusEventPayload) -> None:
        received.append(payload)

    mem.bus.on(BusEvent.TOOL_ABOUT_TO_RUN, on_pre)
    mem.bus.on(BusEvent.TOOL_COMPLETED, on_post)

    user_tool = _make_user_tool("custom_tool")
    agent = _FakeAgent(tools=[user_tool])
    enable_global_tool_bus(mem, agent)

    # Invoke the wrapped tool directly (mirrors what LiveKit does internally).
    await agent.tools[0]._func()
    await asyncio.sleep(0.05)  # let dispatcher drain

    pre = [p for p in received if p.event == BusEvent.TOOL_ABOUT_TO_RUN]
    post = [p for p in received if p.event == BusEvent.TOOL_COMPLETED]
    assert len(pre) == 1, f"expected 1 pre event, got {len(pre)}"
    assert len(post) == 1, f"expected 1 post event, got {len(post)}"
    assert post[0].payload["success"] is True

    await mem.stop_bus()


async def test_global_wrapper_emits_error_on_exception():
    """A raising tool fires tool.completed with success=False + error,
    and the exception propagates (LiveKit error handling preserved).
    """
    mem = FAFMemory("grok", token="t")
    await mem.start_bus()

    received: list[BusEventPayload] = []

    async def on_post(payload: BusEventPayload) -> None:
        received.append(payload)

    mem.bus.on(BusEvent.TOOL_COMPLETED, on_post)

    async def _explode():
        raise RuntimeError("tool blew up")

    user_tool = _make_user_tool("boom_tool", body=_explode)
    agent = _FakeAgent(tools=[user_tool])
    enable_global_tool_bus(mem, agent)

    with pytest.raises(RuntimeError, match="tool blew up"):
        await agent.tools[0]._func()
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].payload["success"] is False
    assert "tool blew up" in received[0].payload["error"]

    await mem.stop_bus()


# ----------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------


async def test_global_wrapper_skips_already_wrapped_factory_tools():
    """FAFMemory factory output is tagged _bus_wrapped=True so the
    global wrapper leaves it alone (no double events on FAFMemory tools).
    """
    mem = FAFMemory("grok", token="t")

    class _FakeSession:
        id = "fake"

        async def say(self, text: str) -> None:
            return None

    factory_tools = mem.tools(_FakeSession())
    # All four factory tools should already be marked.
    for t in factory_tools:
        assert getattr(t, "_bus_wrapped", False) is True, (
            f"{t.info.name} should be marked _bus_wrapped from the factory"
        )

    # Pre-wrap snapshot — what _func currently is.
    pre_funcs = [t._func for t in factory_tools]

    agent = _FakeAgent(tools=list(factory_tools))
    enable_global_tool_bus(mem, agent)

    # Post-wrap _func should be unchanged for factory tools.
    post_funcs = [t._func for t in agent.tools]
    assert pre_funcs == post_funcs, (
        "global wrapper should not re-wrap _bus_wrapped factory tools"
    )


async def test_global_wrapper_idempotent_double_call():
    """Calling enable_global_tool_bus twice doesn't double-wrap user tools."""
    mem = FAFMemory("grok", token="t")
    await mem.start_bus()

    received: list[BusEventPayload] = []

    async def on_post(payload: BusEventPayload) -> None:
        received.append(payload)

    mem.bus.on(BusEvent.TOOL_COMPLETED, on_post)

    user_tool = _make_user_tool("once")
    agent = _FakeAgent(tools=[user_tool])

    enable_global_tool_bus(mem, agent)
    enable_global_tool_bus(mem, agent)  # second call — must be safe

    await agent.tools[0]._func()
    await asyncio.sleep(0.05)

    assert len(received) == 1, (
        f"expected exactly 1 tool.completed event, got {len(received)} — "
        "wrapper double-wrapped"
    )

    await mem.stop_bus()


# ----------------------------------------------------------------
# Metadata preservation
# ----------------------------------------------------------------


def test_global_wrapper_preserves_tool_metadata():
    """FunctionTool's info.name and info.description are still readable
    after the wrapper mutates _func.
    """
    mem = FAFMemory("grok", token="t")

    user_tool = _make_user_tool("named_tool")
    pre_name = user_tool.info.name
    pre_desc = user_tool.info.description

    agent = _FakeAgent(tools=[user_tool])
    enable_global_tool_bus(mem, agent)

    assert agent.tools[0].info.name == pre_name
    assert agent.tools[0].info.description == pre_desc


# ----------------------------------------------------------------
# update_tools patching
# ----------------------------------------------------------------


async def test_update_tools_patched_for_future_additions():
    """Tools added via agent.update_tools() AFTER enable_global_tool_bus
    are also wrapped automatically.
    """
    mem = FAFMemory("grok", token="t")
    await mem.start_bus()

    received: list[BusEventPayload] = []

    async def on_post(payload: BusEventPayload) -> None:
        received.append(payload)

    mem.bus.on(BusEvent.TOOL_COMPLETED, on_post)

    agent = _FakeAgent(tools=[])
    enable_global_tool_bus(mem, agent)

    # Add a tool dynamically AFTER global wrapping is enabled.
    new_tool = _make_user_tool("late_arrival")
    await agent.update_tools([new_tool])

    # The newly-added tool should fire bus events when invoked.
    await agent.tools[0]._func()
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].payload["tool"] == "user_tool"
    # And it should be marked wrapped now.
    assert agent.tools[0]._bus_wrapped is True

    await mem.stop_bus()


async def test_update_tools_patch_idempotent():
    """The update_tools patch isn't applied twice on repeat calls."""
    mem = FAFMemory("grok", token="t")
    agent = _FakeAgent(tools=[])

    enable_global_tool_bus(mem, agent)
    first_update = agent.update_tools

    enable_global_tool_bus(mem, agent)
    second_update = agent.update_tools

    assert first_update is second_update, (
        "update_tools wrapper should be applied at most once"
    )


# ----------------------------------------------------------------
# Sanity: wrapper doesn't break FAFMemory factory tools
# ----------------------------------------------------------------


async def test_wrapper_fires_through_function_tool_call_path():
    """Sanity check the LiveKit-realistic dispatch path.

    LiveKit's generation.py wraps the FunctionTool in functools.partial
    and invokes it as ``await function_callable()`` — which routes
    through ``FunctionTool.__call__`` (lines 210-213 of tool_context.py),
    which reads ``self._func`` at call time. This test confirms our
    ``_func`` swap is hit through that path, not just through direct
    ``tool._func()`` invocation.
    """
    import functools

    mem = FAFMemory("grok", token="t")
    await mem.start_bus()

    received: list[BusEventPayload] = []

    async def on_post(payload: BusEventPayload) -> None:
        received.append(payload)

    mem.bus.on(BusEvent.TOOL_COMPLETED, on_post)

    user_tool = _make_user_tool("dispatch_path")
    agent = _FakeAgent(tools=[user_tool])
    enable_global_tool_bus(mem, agent)

    # Invoke through the same surface LiveKit's dispatch uses.
    function_callable = functools.partial(agent.tools[0])
    await function_callable()
    await asyncio.sleep(0.05)

    assert len(received) == 1, (
        "wrapper must fire through FunctionTool.__call__, "
        "not just through direct ._func() invocation"
    )
    assert received[0].payload["success"] is True

    await mem.stop_bus()


async def test_factory_tools_still_emit_domain_events_after_global_wrap():
    """make_paralinguistic_tool emits paralinguistic.detected on success.
    The global wrapper must not silence that domain-specific event.
    """
    from grok_faf_voice.tools import make_paralinguistic_tool

    mem = FAFMemory("grok", token="t")
    await mem.start_bus()

    received: list[BusEventPayload] = []

    async def on_para(payload: BusEventPayload) -> None:
        received.append(payload)

    mem.bus.on(BusEvent.PARALINGUISTIC_DETECTED, on_para)

    # Stub etch_paralinguistic so the tool body succeeds without MCPaaS.
    async def _stub_etch_para(*args, **kwargs):
        return "ok"

    mem.etch_paralinguistic = _stub_etch_para  # type: ignore[method-assign]

    tool = make_paralinguistic_tool(mem)
    assert getattr(tool, "_bus_wrapped", False) is True

    agent = _FakeAgent(tools=[tool])
    enable_global_tool_bus(mem, agent)

    # Invoke factory tool through the (skipped) wrapper path.
    await agent.tools[0]._func(
        context=None, marker_type="tone", value="calm"
    )
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].payload["marker_type"] == "tone"

    await mem.stop_bus()
