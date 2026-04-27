"""Smoke tests for the Scratchpad primitive."""

from __future__ import annotations

from grok_faf_voice import Scratchpad


def test_import():
    """Scratchpad is importable from the package root."""
    assert Scratchpad is not None


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
