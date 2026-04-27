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


# ----------------------------------------------------------------
# @pytest.mark.network — skip-by-default
#
# Network-marked tests round-trip live against MCPaaS, which writes
# entries into the production soul. Running the whole suite shouldn't
# pollute it. Default behavior: skip. Opt in with ``--run-network``.
# ----------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help=(
            "Run @pytest.mark.network tests (live MCPaaS round-trips). "
            "Skipped by default to avoid soul pollution from CI/dev runs."
        ),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-network"):
        return
    skip_marker = pytest.mark.skip(
        reason="network test skipped — pass --run-network to opt in"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_marker)
