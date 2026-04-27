"""Pydantic models for the Smart Merge Engine.

Wire format for the ``grok-decides`` merge strategy. xAI's structured
outputs binds the chat completion to ``MergeResult``'s JSON schema, so
parse failures are impossible — the response either matches or the
HTTP call errors out before we see it.

These are LLM-shape models (private wire format), distinct from the
SDK's storage primitive ``grok_faf_voice.scratchpad.ScratchpadEntry``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PRIORITY_VALUES = ("ephemeral", "standard", "high", "critical")


class MergePayloadEntry(BaseModel):
    """Scratchpad entry as sent to the LLM (wire format)."""

    id: str
    content: str
    priority: str = Field(
        ..., description="ephemeral | standard | high | critical"
    )
    tags: list[str] = Field(default_factory=list)
    timestamp: datetime | None = None
    source: str | None = None


class ExistingMemorySummary(BaseModel):
    """Lightweight view of permanent soul memories the model may merge into."""

    id: str
    summary: str


class MergeDecision(BaseModel):
    entry_id: str = Field(
        ..., description="ID of the scratchpad entry being decided"
    )
    action: Literal["promote", "keep_ephemeral", "merge_into"] = Field(
        ...,
        description=(
            "promote = move to permanent soul memory, "
            "keep_ephemeral = discard after session, "
            "merge_into = combine with another entry"
        ),
    )
    target_id: str | None = Field(
        None,
        description=(
            "Only required when action='merge_into'. Must be a valid ID "
            "from the current scratchpad batch or the existing_memories list."
        ),
    )
    tags: list[str] = Field(
        ...,
        description=(
            "Final tags to apply. Always include 'merged' when action "
            "is promote or merge_into."
        ),
    )
    priority: Literal["ephemeral", "standard", "high", "critical"] = Field(
        ..., description="Final priority after decision"
    )
    rationale: str = Field(
        ...,
        min_length=10,
        max_length=400,
        description="Concise 1-3 sentence justification.",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in decision (0.0-1.0)"
    )


class MergeResult(BaseModel):
    decisions: list[MergeDecision] = Field(..., min_length=1)
    overall_notes: str | None = Field(
        None,
        description="Optional high-level observations about the entire batch.",
    )


SYSTEM_PROMPT = """You are Grok's senior memory consolidation agent.

Your sole job is to decide the long-term fate of voice scratchpad entries so the user's "soul" (persistent memory) stays clean, accurate, and high-signal.

Rules (follow strictly):
- Be conservative: only promote or merge items that are clearly valuable for future conversations.
- Prefer merge_into over promote when two entries cover the same topic or can be combined without losing meaning.
- Never invent target_ids. Only use IDs that appear in the provided scratchpad entries or existing_memories list.
- Every promoted or merged entry must receive the tag "merged".
- Keep ephemeral anything that is low-value, redundant, sensitive, or likely to become stale quickly.
- Output ONLY the structured MergeResult — no extra text outside the JSON schema.
"""  # noqa: E501


USER_PROMPT_TEMPLATE = """Current session context:
- User soul summary: {soul_summary}
- Number of scratchpad entries: {num_entries}

Scratchpad entries to evaluate:
{scratchpad_json}

Existing permanent soul memories you may merge into (if relevant):
{existing_memories_json}

Decide the fate of every entry using the MergeResult schema.
"""
