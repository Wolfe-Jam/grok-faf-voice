"""Smoke tests for the LiveKit @function_tool wrappers."""

from __future__ import annotations

from grok_faf_voice import FAFMemory
from grok_faf_voice.tools import make_etch_tool, make_recall_tool


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


def test_tools_factory_pair_distinct():
    """The two tools are distinct objects."""
    mem = FAFMemory("grok")
    etch_tool = make_etch_tool(mem)
    recall_tool = make_recall_tool(mem)
    assert etch_tool is not recall_tool


def test_fafmemory_tools_returns_pair():
    """FAFMemory.tools() returns the etch + recall pair."""
    mem = FAFMemory("grok")
    tools = mem.tools()
    assert len(tools) == 2
