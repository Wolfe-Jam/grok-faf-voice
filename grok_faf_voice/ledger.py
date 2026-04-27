"""Voice Session Ledger — audit + cross-session resumption.

Records every Smart Merge attempt (success and failure) so that:

- Auditing: callers can reconstruct what was merged when, at which session
- Resumption (Gate 5+): a future session can read incomplete merges and
  retry the failed entries instead of silently dropping them

The Protocol is the SDK's contract; concrete implementations are the
caller's choice. The SDK ships ``NullVoiceSessionLedger`` (default,
no-op) and ``InMemoryVoiceSessionLedger`` (process-scoped, useful for
tests + small demos). MCPaaS-backed and SQLite-backed implementations
are downstream-of-here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol


class VoiceSessionLedger(Protocol):
    """Protocol for recording Smart Merge attempts."""

    async def log_merge_attempt(
        self,
        *,
        session_id: str,
        status: str,
        promoted: int = 0,
        merged: int = 0,
        kept_ephemeral: int = 0,
        overall_notes: str | None = None,
        error: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Record a single merge attempt.

        Parameters
        ----------
        session_id : str
            The LiveKit ``AgentSession.id`` (or any unique identifier).
        status : str
            One of ``"completed"``, ``"partial_or_failed"``, ``"partial"``,
            ``"failed"``. Free-form to allow future statuses without a
            Protocol breaking change.
        promoted : int
            Count of scratchpad entries promoted to permanent memory.
        merged : int
            Count of entries consolidated via ``merge_into`` (Gate 5+).
            Always 0 at Gate 4.5 — ``merge_into`` collapses to ``promote``.
        kept_ephemeral : int
            Count of entries explicitly kept ephemeral (not promoted).
        overall_notes : str, optional
            High-level observations from the LLM (grok-decides only).
        error : str, optional
            Error message when ``status`` indicates failure.
        timestamp : datetime, optional
            Defaults to ``datetime.now(timezone.utc)`` if omitted.
        """
        ...


class NullVoiceSessionLedger:
    """Default ledger — drops every write. Use when audit isn't needed."""

    async def log_merge_attempt(self, **kwargs: Any) -> None:
        return None


class InMemoryVoiceSessionLedger:
    """Process-scoped ledger — keeps every attempt in a list.

    Useful for tests, demos, and short-lived sessions. Not durable —
    state dies with the process. For production audit, swap in a
    persistent implementation (MCPaaS-backed, SQLite, etc.).

    Examples
    --------
    >>> ledger = InMemoryVoiceSessionLedger()
    >>> mem = FAFMemory("grok", token="...", ledger=ledger)
    >>> # ... session runs, merge fires ...
    >>> ledger.attempts[0]["status"]
    'completed'
    """

    def __init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []

    async def log_merge_attempt(self, **kwargs: Any) -> None:
        record = dict(kwargs)
        if record.get("timestamp") is None:
            record["timestamp"] = datetime.now(timezone.utc)
        self.attempts.append(record)
