"""WJTTC ENGINE-tier tests — local souls (v0.3.0 ``from_file`` / ``to_file``).

Pure, deterministic, offline: no MCPaaS, no API key, no network. Verifies the
local-first ``.fafm`` read/write path and the cross-vendor read (a
knowledge-profile soul written by Claude-side tooling loads and recalls
cleanly in grok-faf-voice).

Tier: ENGINE (correctness). Rides pytest; runs on every commit / PR.
Companion to the live MCPaaS roundtrip (TYRES tier, network-marked).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from grok_faf_voice import FAFMemory

# --- fixtures: minimal valid souls for each profile -----------------------

VOICE_SOUL = """\
version: "1.1"
profile: "voice"
namepoint: "@demo-user"
created: "2026-04-30T12:00:00Z"
last_etched: "2026-05-21T09:00:00Z"
retention: "forever"
memory:
  facts:
    - "User prefers short answers"
    - text: "User's name is Alex"
      tags: ["personal"]
"""

# Shaped like a Claude-side knowledge soul: rich facts with id/type/priority/
# links + a top-level index. The cross-vendor read target.
KNOWLEDGE_SOUL = """\
version: "1.1"
profile: "knowledge"
namepoint: "@claude-code:wolfejam"
created: "2026-05-21T00:00:00Z"
last_etched: "2026-05-21T09:00:00Z"
retention: "forever"
index:
  - "precision-is-power — named tiers beat umbrella terms"
memory:
  facts:
    - text: "Replace lossy umbrella terms with named tiers."
      id: "precision-is-power"
      type: "feedback"
      priority: "high"
      tags: ["copy", "doctrine"]
      links: ["no-made-up-numbers"]
      timestamp: "2026-05-20T00:00:00Z"
      source: "session note"
"""


@pytest.fixture
def voice_soul_path(tmp_path: Path) -> Path:
    p = tmp_path / "voice.fafm"
    p.write_text(VOICE_SOUL, encoding="utf-8")
    return p


@pytest.fixture
def knowledge_soul_path(tmp_path: Path) -> Path:
    p = tmp_path / "knowledge.fafm"
    p.write_text(KNOWLEDGE_SOUL, encoding="utf-8")
    return p


# --- namepoint / soul-id extraction ---------------------------------------

def test_from_file_extracts_quoted_namepoint(knowledge_soul_path):
    """Soul id is parsed from a quoted, namespaced namepoint."""
    mem = FAFMemory.from_file(knowledge_soul_path)
    assert mem.soul == "@claude-code:wolfejam"


def test_from_file_extracts_unquoted_namepoint(tmp_path):
    """Soul id is parsed from an unquoted namepoint."""
    p = tmp_path / "x.fafm"
    p.write_text("version: '1.1'\nnamepoint: @demo\nmemory:\n  facts: []\n")
    mem = FAFMemory.from_file(p)
    assert mem.soul == "@demo"


def test_from_file_falls_back_to_stem_without_namepoint(tmp_path):
    """No namepoint in the document → soul id falls back to the file stem."""
    p = tmp_path / "orphan.fafm"
    p.write_text("version: '1.1'\nmemory:\n  facts: []\n")
    mem = FAFMemory.from_file(p)
    assert mem.soul == "orphan"


# --- local mode vs MCPaaS (back-compat guard) -----------------------------

def test_default_constructor_is_mcpaas_mode():
    """A normal FAFMemory has no local_path → MCPaaS behavior unchanged."""
    mem = FAFMemory("grok")
    assert mem._local_path is None


def test_from_file_enables_local_mode(voice_soul_path):
    """from_file sets local mode."""
    mem = FAFMemory.from_file(voice_soul_path)
    assert mem._local_path is not None


# --- get() reads off disk, no creds, no network ---------------------------

async def test_get_returns_exact_file_contents(knowledge_soul_path):
    """Local get() returns the file's exact bytes — no MCPaaS round-trip."""
    with patch.dict(os.environ, {}, clear=True):  # prove: no creds needed
        mem = FAFMemory.from_file(knowledge_soul_path)
        body = await mem.get()
    assert body == KNOWLEDGE_SOUL


async def test_get_local_needs_no_api_key(voice_soul_path):
    """Local read works with no MCPAAS_API_KEY set."""
    with patch.dict(os.environ, {}, clear=True):
        mem = FAFMemory.from_file(voice_soul_path)
        assert mem._api_key is None
        body = await mem.get()
    assert "namepoint" in body


# --- to_file roundtrip -----------------------------------------------------

async def test_to_file_roundtrip_byte_identical(knowledge_soul_path, tmp_path):
    """from_file → to_file produces a byte-identical document."""
    mem = FAFMemory.from_file(knowledge_soul_path)
    out = await mem.to_file(tmp_path / "out.fafm")
    assert Path(out).read_text(encoding="utf-8") == knowledge_soul_path.read_text(
        encoding="utf-8"
    )


# --- recall_for_prompt on a local soul ------------------------------------

async def test_recall_includes_header_and_body(knowledge_soul_path):
    """recall_for_prompt wraps the local soul body with the prompt header."""
    mem = FAFMemory.from_file(knowledge_soul_path)
    recall = await mem.recall_for_prompt()
    assert "prior sessions" in recall  # default header
    assert "Replace lossy umbrella terms" in recall  # fact text


async def test_recall_uses_whole_text_without_separator(voice_soul_path):
    """Pure-YAML .fafm has no MCPaaS '\\n---\\n' preamble → whole text used."""
    mem = FAFMemory.from_file(voice_soul_path)
    recall = await mem.recall_for_prompt()
    assert "User prefers short answers" in recall


# --- profile-agnostic ------------------------------------------------------

async def test_voice_profile_loads(voice_soul_path):
    mem = FAFMemory.from_file(voice_soul_path)
    body = await mem.get()
    assert "voice" in body and "facts" in body


async def test_knowledge_profile_loads(knowledge_soul_path):
    mem = FAFMemory.from_file(knowledge_soul_path)
    body = await mem.get()
    assert "knowledge" in body and "facts" in body


# --- cross-vendor read (Arc 2 cornerstone, locked as a regression) --------

async def test_cross_vendor_read_claude_knowledge_soul(knowledge_soul_path):
    """A knowledge-profile soul in the Claude-side shape loads and recalls in
    grok-faf-voice — offline, no creds. Locks the cross-vendor interop proof
    as a permanent regression test."""
    with patch.dict(os.environ, {}, clear=True):
        mem = FAFMemory.from_file(knowledge_soul_path)
        assert mem.soul == "@claude-code:wolfejam"
        recall = await mem.recall_for_prompt()
    assert "precision-is-power" in recall  # rich-fact id present in body
    assert "named tiers" in recall  # index hook present
