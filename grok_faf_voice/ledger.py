"""Voice Session Ledger — audit + cross-session resumption.

Records every Smart Merge attempt (success and failure) so that:

- Auditing: callers can reconstruct what was merged when, at which session
- Resumption: a future session can read incomplete merges and retry
  the failed entries instead of silently dropping them

The Protocol is the SDK's contract; concrete implementations are the
caller's choice. The SDK ships ``NullVoiceSessionLedger`` (default,
no-op) and ``InMemoryVoiceSessionLedger`` (process-scoped, useful for
tests + small demos). MCPaaS-backed and SQLite-backed implementations
are downstream-of-here.

## Idempotency contract

``log_merge_attempt`` returns the ``merge_attempt_id`` of the recorded
attempt. When the caller passes ``merge_attempt_id=...`` on subsequent
calls (e.g. retries), the ledger UPDATES the existing record instead of
creating a new one. This is how cross-session resumption stays
deduplicated — every retry of attempt X is recorded against the same id.

## Resumption flow

1. ``FAFMemory.on_session_start(session)`` calls
   ``ledger.get_incomplete_merges(soul, max_age_hours=72)``
2. For each non-completed attempt within the window, the SDK calls
   ``mark_merge_resumed(merge_attempt_id)`` then retries the merge,
   logging the outcome with the same ``merge_attempt_id``.
3. Old attempts (>72h) are skipped silently; very-failed attempts
   (retry_count ≥ 3) are abandoned with a final ``"failed"`` status.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

# Statuses the ledger may record. Free-form strings allow forward-compat;
# these are the canonical values used by the SDK.
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_PARTIAL_OR_FAILED = "partial_or_failed"
STATUS_FAILED = "failed"
STATUS_IN_PROGRESS = "in_progress"

# Statuses considered "incomplete" — eligible for resumption.
INCOMPLETE_STATUSES = frozenset({STATUS_PARTIAL, STATUS_PARTIAL_OR_FAILED})


@dataclass
class MergeAttempt:
    """One Smart Merge attempt — past, present, or being retried.

    Stored in the ledger and returned from ``get_incomplete_merges``.
    The ``merge_attempt_id`` is the idempotency key: passing it back
    into ``log_merge_attempt`` updates this record rather than
    appending a new one.
    """

    merge_attempt_id: str
    session_id: str
    soul: str
    status: str
    promoted: int = 0
    merged: int = 0
    kept_ephemeral: int = 0
    failed_entry_ids: list[str] = field(default_factory=list)
    retry_count: int = 0
    last_attempt_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    overall_notes: str | None = None
    error: str | None = None


class VoiceSessionLedger(Protocol):
    """Protocol for recording Smart Merge attempts + supporting resumption."""

    async def log_merge_attempt(
        self,
        *,
        session_id: str,
        status: str,
        soul: str = "",
        promoted: int = 0,
        merged: int = 0,
        kept_ephemeral: int = 0,
        failed_entry_ids: list[str] | None = None,
        retry_count: int = 0,
        overall_notes: str | None = None,
        error: str | None = None,
        timestamp: datetime | None = None,
        merge_attempt_id: str | None = None,
    ) -> str:
        """Record a single merge attempt.

        When ``merge_attempt_id`` is provided, UPDATES the existing
        record (idempotent retries). When omitted, CREATES a new record
        and assigns a fresh id.

        Returns
        -------
        str
            The ``merge_attempt_id`` of the recorded attempt.
        """
        ...

    async def get_incomplete_merges(
        self, *, soul: str, max_age_hours: int = 72
    ) -> list[MergeAttempt]:
        """Return non-completed merges for ``soul`` within the window.

        "Non-completed" means status in
        ``{"partial", "partial_or_failed"}``. Older than
        ``max_age_hours`` are excluded.
        """
        ...

    async def mark_merge_resumed(
        self, *, merge_attempt_id: str, new_status: str = STATUS_IN_PROGRESS
    ) -> None:
        """Mark an attempt as being retried."""
        ...


class NullVoiceSessionLedger:
    """Default ledger — drops every write. Use when audit isn't needed."""

    async def log_merge_attempt(self, **kwargs: Any) -> str:
        # Return a fresh id so callers depending on the contract don't
        # crash, but persist nothing.
        return kwargs.get("merge_attempt_id") or str(uuid.uuid4())

    async def get_incomplete_merges(
        self, *, soul: str, max_age_hours: int = 72
    ) -> list[MergeAttempt]:
        return []

    async def mark_merge_resumed(
        self, *, merge_attempt_id: str, new_status: str = STATUS_IN_PROGRESS
    ) -> None:
        return None


class InMemoryVoiceSessionLedger:
    """Process-scoped ledger — keeps every attempt in a list, with
    idempotent updates by ``merge_attempt_id``.

    Useful for tests, demos, and short-lived sessions. Not durable —
    state dies with the process. For production audit, swap in a
    persistent implementation (MCPaaS-backed, SQLite, etc.).

    Examples
    --------
    >>> ledger = InMemoryVoiceSessionLedger()
    >>> mem = FAFMemory("grok", api_key="...", ledger=ledger)
    >>> # ... session runs, merge fires ...
    >>> ledger.attempts[0]["status"]
    'completed'
    """

    def __init__(self) -> None:
        # Backing store keyed by merge_attempt_id for O(1) idempotent updates.
        # ``attempts`` is the public list view (insertion order preserved).
        self._records: dict[str, dict[str, Any]] = {}
        self.attempts: list[dict[str, Any]] = []

    async def log_merge_attempt(self, **kwargs: Any) -> str:
        merge_attempt_id = kwargs.pop("merge_attempt_id", None)
        timestamp = kwargs.get("timestamp")
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
            kwargs["timestamp"] = timestamp

        if merge_attempt_id and merge_attempt_id in self._records:
            # Idempotent UPDATE — existing record gets new field values.
            existing = self._records[merge_attempt_id]
            existing.update(kwargs)
            existing["merge_attempt_id"] = merge_attempt_id
            existing["last_attempt_at"] = timestamp
            return merge_attempt_id

        # New record — assign id if none provided.
        if not merge_attempt_id:
            merge_attempt_id = str(uuid.uuid4())
        record: dict[str, Any] = dict(kwargs)
        record["merge_attempt_id"] = merge_attempt_id
        record.setdefault("created_at", timestamp)
        record["last_attempt_at"] = timestamp
        self._records[merge_attempt_id] = record
        self.attempts.append(record)
        return merge_attempt_id

    async def get_incomplete_merges(
        self, *, soul: str, max_age_hours: int = 72
    ) -> list[MergeAttempt]:
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        out: list[MergeAttempt] = []
        for rec in self.attempts:
            if rec.get("soul", "") != soul:
                continue
            if rec.get("status") not in INCOMPLETE_STATUSES:
                continue
            ts = rec.get("last_attempt_at") or rec.get("timestamp")
            if isinstance(ts, datetime) and ts.timestamp() < cutoff:
                continue
            out.append(self._record_to_merge_attempt(rec))
        return out

    async def mark_merge_resumed(
        self, *, merge_attempt_id: str, new_status: str = STATUS_IN_PROGRESS
    ) -> None:
        if merge_attempt_id in self._records:
            self._records[merge_attempt_id]["status"] = new_status

    @staticmethod
    def _record_to_merge_attempt(rec: dict[str, Any]) -> MergeAttempt:
        """Project a stored dict into the MergeAttempt dataclass."""
        return MergeAttempt(
            merge_attempt_id=rec["merge_attempt_id"],
            session_id=rec.get("session_id", ""),
            soul=rec.get("soul", ""),
            status=rec.get("status", "unknown"),
            promoted=rec.get("promoted", 0),
            merged=rec.get("merged", 0),
            kept_ephemeral=rec.get("kept_ephemeral", 0),
            failed_entry_ids=list(rec.get("failed_entry_ids") or []),
            retry_count=rec.get("retry_count", 0),
            last_attempt_at=rec.get("last_attempt_at")
            or datetime.now(timezone.utc),
            created_at=rec.get("created_at") or datetime.now(timezone.utc),
            overall_notes=rec.get("overall_notes"),
            error=rec.get("error"),
        )
