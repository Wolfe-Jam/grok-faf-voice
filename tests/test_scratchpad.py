"""Smoke tests for the Scratchpad primitive."""

from __future__ import annotations

from grok_faf_voice import Scratchpad, ScratchpadEntry


def test_import():
    """Scratchpad and ScratchpadEntry are importable from the package root."""
    assert Scratchpad is not None
    assert ScratchpadEntry is not None


def test_starts_empty():
    """A fresh Scratchpad has no entries."""
    pad = Scratchpad()
    assert len(pad) == 0
    assert pad.all() == {}


def test_update_and_get():
    """update() stores; get() retrieves."""
    pad = Scratchpad()
    pad.update("address", "123 Main St")
    assert pad.get("address") == "123 Main St"


def test_get_missing_returns_none():
    """get() returns None for missing keys."""
    pad = Scratchpad()
    assert pad.get("nope") is None


def test_all_returns_copy():
    """all() returns a shallow copy, not the live dict."""
    pad = Scratchpad()
    pad.update("k", "v")
    snapshot = pad.all()
    snapshot["k"] = "mutated"
    assert pad.get("k") == "v"


def test_clear():
    """clear() empties the scratchpad."""
    pad = Scratchpad()
    pad.update("a", "1")
    pad.update("b", "2")
    pad.clear()
    assert len(pad) == 0


def test_contains():
    """`in` operator works on scratchpad keys."""
    pad = Scratchpad()
    pad.update("present", "yes")
    assert "present" in pad
    assert "absent" not in pad


# ---- ScratchpadEntry metadata ----


def test_default_priority_is_medium():
    """update() with no priority kwarg defaults to 'medium'."""
    pad = Scratchpad()
    pad.update("k", "v")
    entry = pad.get_entry("k")
    assert entry.priority == "medium"
    assert entry.tag is None


def test_explicit_priority_and_tag():
    """priority and tag flow through update()."""
    pad = Scratchpad()
    pad.update("addr", "123 Main St", priority="high", tag="contact")
    entry = pad.get_entry("addr")
    assert entry.value == "123 Main St"
    assert entry.priority == "high"
    assert entry.tag == "contact"


def test_get_entry_returns_none_for_missing():
    """get_entry returns None on missing keys."""
    pad = Scratchpad()
    assert pad.get_entry("nope") is None


def test_all_entries_returns_dataclass_dict():
    """all_entries() returns key → ScratchpadEntry mapping."""
    pad = Scratchpad()
    pad.update("a", "1", priority="high")
    pad.update("b", "2", priority="ephemeral")
    entries = pad.all_entries()
    assert isinstance(entries["a"], ScratchpadEntry)
    assert entries["a"].priority == "high"
    assert entries["b"].priority == "ephemeral"


def test_backward_compat_all_returns_value_dict():
    """all() preserves the Gate-2 shape ({key: value})."""
    pad = Scratchpad()
    pad.update("k", "v", priority="high")
    assert pad.all() == {"k": "v"}
