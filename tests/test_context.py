"""Smoke tests for FAFContext."""

from __future__ import annotations

from pathlib import Path

import pytest

from grok_faf_voice import FAFContext


def test_import():
    """FAFContext is importable from the package root."""
    assert FAFContext is not None


def test_local_file_load(tmp_faf_path: Path):
    """FAFContext reads a local .faf file (uses shared fixture)."""
    ctx = FAFContext(str(tmp_faf_path))
    content = ctx.load()

    assert "name: test-project" in content


def test_system_prompt_returns_string(tmp_faf_path: Path):
    """system_prompt() returns a string containing the .faf content."""
    ctx = FAFContext(str(tmp_faf_path))
    prompt = ctx.system_prompt()

    assert isinstance(prompt, str)
    assert "name: test-project" in prompt
    assert "BEGIN .faf" in prompt
    assert "END .faf" in prompt


def test_load_is_idempotent(tmp_path: Path):
    """Calling load() twice returns the same cached content."""
    faf_path = tmp_path / "test.faf"
    faf_path.write_text("project:\n  name: cached\n")

    ctx = FAFContext(str(faf_path))
    first = ctx.load()
    second = ctx.load()

    assert first == second


@pytest.mark.network
def test_mcpaas_slug_load():
    """FAFContext fetches a slug from MCPaaS. Live network test."""
    ctx = FAFContext("faf")  # meta-soul, confirmed live 2026-04-26
    content = ctx.load()

    # Response is raw YAML; root keys vary by soul shape.
    assert isinstance(content, str)
    assert len(content) > 0
