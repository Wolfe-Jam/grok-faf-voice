"""Smoke tests for the LiveKit @function_tool wrappers."""

from __future__ import annotations

from grok_faf_voice import FAFMemory
from grok_faf_voice.tools import (
    make_etch_tool,
    make_merge_tool,
    make_paralinguistic_tool,
    make_recall_tool,
)


class _FakeSession:
    """Minimal stand-in for AgentSession — just needs to be passable
    as the ``session`` argument to ``make_merge_tool``. The factory
    only stores the reference; tool execution would need ``.say(...)``
    but smoke tests don't invoke the tool body.
    """

    id = "fake-session"

    async def say(self, text: str) -> None:
        return None


def test_make_etch_tool_returns_object():
    """make_etch_tool returns a FunctionTool object."""
    mem = FAFMemory("grok")
    tool = make_etch_tool(mem)
    assert tool is not None


def test_make_recall_tool_returns_object():
    """make_recall_tool returns a FunctionTool object."""
    mem = FAFMemory("grok")
    tool = make_recall_tool(mem)
    assert tool is not None


def test_make_paralinguistic_tool_returns_object():
    """make_paralinguistic_tool returns a FunctionTool object."""
    mem = FAFMemory("grok")
    tool = make_paralinguistic_tool(mem)
    assert tool is not None


def test_make_merge_tool_returns_object():
    """make_merge_tool returns a FunctionTool object."""
    mem = FAFMemory("grok")
    tool = make_merge_tool(mem, _FakeSession())
    assert tool is not None


def test_tools_factory_set_distinct():
    """The memory tools are distinct objects. forget is not one of them."""
    from grok_faf_voice.tools import make_confirm_tool

    mem = FAFMemory("grok")
    etch = make_etch_tool(mem)
    confirm = make_confirm_tool(mem)
    recall = make_recall_tool(mem)
    para = make_paralinguistic_tool(mem)
    merge = make_merge_tool(mem, _FakeSession())
    assert len({id(etch), id(confirm), id(recall), id(para), id(merge)}) == 5


def test_fafmemory_tools_returns_the_set_without_forget():
    """tools(session) exposes confirm, and does not expose forget."""
    mem = FAFMemory("grok")
    tools = mem.tools(_FakeSession())
    assert len(tools) == 5
    names = {t.info.name for t in tools}
    assert names == {
        "etch_memory",
        "confirm_memory",
        "recall_memory",
        "note_paralinguistic",
        "merge_now",
    }
    assert "forget" not in names
    assert not any("forget" in name for name in names)
