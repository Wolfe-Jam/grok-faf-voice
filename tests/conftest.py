"""Shared pytest fixtures for grok-faf-voice tests.

Add fixtures here as the test pyramid grows. Keep them single-purpose;
tests should compose fixtures, not have to re-build them inline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SAMPLE_FAF_CONTENT = """\
faf_version: "3.0"
project:
  name: test-project
  goal: A sample project for testing
  main_language: Python
  type: sdk
human_context:
  who: Test devs
  what: Test SDK
  why: Test purposes
  where: pytest
  when: 2026-04-26
  how: pytest fixtures
"""


@pytest.fixture
def sample_faf_content() -> str:
    """A complete, valid sample .faf YAML body for tests."""
    return SAMPLE_FAF_CONTENT


@pytest.fixture
def tmp_faf_path(tmp_path: Path, sample_faf_content: str) -> Path:
    """Write a sample .faf to a tmp dir and return the path."""
    path = tmp_path / "project.faf"
    path.write_text(sample_faf_content)
    return path
