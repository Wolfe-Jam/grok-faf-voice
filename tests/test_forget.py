"""Operator forget: each criterion, local file and hosted soul. Not a tool."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from grok_faf_voice import FAFForgetError, FAFMemory, ForgetCriteria

VOICE = """\
version: "1.1"
profile: "voice"
namepoint: "@demo-user"
memory:
  facts:
    - "User prefers short answers"
    - text: "User's name is Alex"
      tags: ["personal"]
"""

KNOWLEDGE = """\
version: "1.1"
profile: "knowledge"
namepoint: "@claude-code:wolfejam"
index:
  - "precision-is-power — named tiers beat umbrella terms"
memory:
  facts:
    - text: "Replace lossy umbrella terms with named tiers."
      id: "precision-is-power"
      tags: ["copy", "doctrine"]
      timestamp: "2026-05-20T00:00:00Z"
    - "undated string fact"
"""

LINES = """\
[facts]
# Fact:
✓ keep this [stay]
• drop this id: gone 2026-01-01T00:00:00Z [old]
• other id: stay 2026-06-01T00:00:00Z [stay]
"""


def _facts(path: Path) -> list:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc["memory"]["facts"]


def test_forget_is_not_a_model_tool():
    mem = FAFMemory("grok")
    names = {tool.info.name for tool in mem.tools(_Session())}
    assert "forget" not in names
    assert "confirm_memory" in names


class _Session:
    id = "s"

    async def say(self, text: str) -> None:
        return None


async def test_empty_criteria_raises_and_keeps_the_file(tmp_path: Path):
    path = tmp_path / "voice.fafm"
    path.write_text(VOICE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    with pytest.raises(FAFForgetError):
        await mem.forget("@demo-user", ForgetCriteria())
    assert path.read_text(encoding="utf-8") == VOICE


async def test_namepoint_mismatch_does_not_write(tmp_path: Path):
    path = tmp_path / "voice.fafm"
    path.write_text(VOICE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    with pytest.raises(FAFForgetError):
        await mem.forget("@someone-else", ForgetCriteria(wipe=True))
    assert path.read_text(encoding="utf-8") == VOICE


async def test_forget_by_entry_id(tmp_path: Path):
    path = tmp_path / "knowledge.fafm"
    path.write_text(KNOWLEDGE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    result = await mem.forget(
        "claude-code:wolfejam",
        ForgetCriteria(entry_id="precision-is-power"),
    )
    assert result["removed"] == 1
    facts = _facts(path)
    assert len(facts) == 1
    assert facts[0] == "undated string fact"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["index"]


async def test_forget_by_tag_keeps_string_facts(tmp_path: Path):
    path = tmp_path / "voice.fafm"
    path.write_text(VOICE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    result = await mem.forget("@DEMO-USER", ForgetCriteria(tag="personal"))
    assert result["removed"] == 1
    facts = _facts(path)
    assert facts == ["User prefers short answers"]


async def test_forget_by_time_range_is_inclusive_and_skips_undated(tmp_path: Path):
    path = tmp_path / "knowledge.fafm"
    path.write_text(KNOWLEDGE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    start = datetime(2026, 5, 20, tzinfo=timezone.utc)
    end = datetime(2026, 5, 20, tzinfo=timezone.utc)
    result = await mem.forget("@claude-code:wolfejam", ForgetCriteria(start=start, end=end))
    assert result["removed"] == 1
    assert _facts(path) == ["undated string fact"]


async def test_time_range_outside_keeps_the_dated_fact(tmp_path: Path):
    path = tmp_path / "knowledge.fafm"
    path.write_text(KNOWLEDGE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    result = await mem.forget(
        "@claude-code:wolfejam",
        ForgetCriteria(start="2026-06-01T00:00:00Z", end="2026-06-02T00:00:00Z"),
    )
    assert result["removed"] == 0
    assert path.read_text(encoding="utf-8") == KNOWLEDGE


async def test_criteria_and_together(tmp_path: Path):
    path = tmp_path / "knowledge.fafm"
    path.write_text(KNOWLEDGE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    result = await mem.forget(
        "@claude-code:wolfejam",
        ForgetCriteria(entry_id="precision-is-power", tag="missing"),
    )
    assert result["removed"] == 0
    assert path.read_text(encoding="utf-8") == KNOWLEDGE


async def test_wipe_clears_facts_and_index_and_keeps_the_file(tmp_path: Path):
    path = tmp_path / "knowledge.fafm"
    path.write_text(KNOWLEDGE, encoding="utf-8")
    mem = FAFMemory.from_file(path)
    result = await mem.forget("@claude-code:wolfejam", ForgetCriteria(wipe=True))
    assert result["removed"] == 3  # two facts + one index line
    assert path.exists()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["memory"]["facts"] == []
    assert doc["index"] == []
    assert doc["namepoint"] == "@claude-code:wolfejam"


async def test_line_soul_forgets_by_id_tag_and_time():
    from grok_faf_voice.forget import apply_forget

    by_id, removed = apply_forget(LINES, ForgetCriteria(entry_id="gone"))
    assert removed == 1
    assert "id: gone" not in by_id
    assert "keep this" in by_id

    by_tag, removed = apply_forget(LINES, ForgetCriteria(tag="old"))
    assert removed == 1
    assert "[old]" not in by_tag

    by_time, removed = apply_forget(
        LINES,
        ForgetCriteria(start="2026-01-01T00:00:00Z", end="2026-01-01T00:00:00Z"),
    )
    assert removed == 1
    assert "2026-01-01" not in by_time
    assert "2026-06-01" in by_time

    wiped, removed = apply_forget(LINES, ForgetCriteria(wipe=True))
    assert removed == 5  # two scaffolds + three entry lines
    assert "✓" not in wiped
    assert "# Fact:" not in wiped


class _Result:
    def __init__(self, text: str) -> None:
        self.content = [type("T", (), {"text": text})()]


class _Client:
    calls: list[tuple[str, dict]] = []

    def __init__(self, transport) -> None:
        self.transport = transport

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def call_tool(self, name, args):
        _Client.calls.append((name, dict(args)))
        if name == "get_soul":
            body = "server preamble\n---\n" + KNOWLEDGE
            return _Result(body)
        if name == "delete_soul":
            raise AssertionError("delete_soul is not forget")
        return _Result("replaced")


async def test_hosted_forget_replaces_and_strips_the_preamble(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr("grok_faf_voice.memory.Client", _Client)
    mem = FAFMemory("@claude-code:wolfejam", api_key="k")
    result = await mem.forget(
        "@claude-code:wolfejam",
        ForgetCriteria(entry_id="precision-is-power"),
    )
    assert result["removed"] == 1
    names = [name for name, _args in _Client.calls]
    assert names == ["get_soul", "write_soul"]
    assert "delete_soul" not in names
    write = _Client.calls[1][1]
    assert write["replace"] is True
    assert "server preamble" not in write["content"]
    written = yaml.safe_load(write["content"])
    ids = [
        fact.get("id")
        for fact in written["memory"]["facts"]
        if isinstance(fact, dict)
    ]
    assert "precision-is-power" not in ids
    assert "undated string fact" in written["memory"]["facts"]


async def test_hosted_forget_does_not_write_when_nothing_matches(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr("grok_faf_voice.memory.Client", _Client)
    mem = FAFMemory("wolfe", api_key="k")
    # The hosted body is the knowledge soul, whose namepoint differs.
    # Bind the memory to the same namepoint the document uses.
    mem._soul = "@claude-code:wolfejam"
    result = await mem.forget(
        "@claude-code:wolfejam",
        ForgetCriteria(entry_id="missing"),
    )
    assert result["removed"] == 0
    assert [name for name, _args in _Client.calls] == ["get_soul"]


async def test_hosted_namepoint_mismatch_does_not_call_mcp():
    mem = FAFMemory("wolfe", api_key="k")
    with pytest.raises(FAFForgetError):
        await mem.forget("@other", ForgetCriteria(wipe=True))
