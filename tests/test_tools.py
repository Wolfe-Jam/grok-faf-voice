"""Smoke tests for the LiveKit @function_tool wrappers."""

from __future__ import annotations

from grok_faf_voice import FAFMemory
from grok_faf_voice.tools import (
    make_etch_tool,
    make_merge_tool,
    make_paralinguistic_tool,
    make_recall_tool,
)


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
    tool = make_merge_tool(mem)
    assert tool is not None


def test_tools_factory_quartet_distinct():
    """All four tools are distinct objects."""
    mem = FAFMemory("grok")
    etch = make_etch_tool(mem)
    recall = make_recall_tool(mem)
    para = make_paralinguistic_tool(mem)
    merge = make_merge_tool(mem)
    assert len({id(etch), id(recall), id(para), id(merge)}) == 4


def test_fafmemory_tools_returns_quartet():
    """FAFMemory.tools() returns etch + recall + paralinguistic + merge_now."""
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
