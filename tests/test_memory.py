"""Smoke tests for FAFMemory skeleton (Gate 1).

FAFMemory's full surface ships at Gates 2–6. These tests verify the
public API exists and is importable from v0.0.1.
"""

from __future__ import annotations

from grok_faf_voice import FAFMemory


def test_import():
    """FAFMemory is importable from the package root."""
    assert FAFMemory is not None


def test_instantiation():
    """FAFMemory accepts a string argument."""
    mem = FAFMemory("project.fafm")
    assert mem is not None
