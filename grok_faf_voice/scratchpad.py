"""Scratchpad — in-memory store for the current voice session.

Gate 2 surface: simple key/value store + manual update API.
Gate 4 enrichment: each entry carries a ``ScratchpadEntry`` with
``priority`` and ``tag`` metadata so ``FAFMemory.merge()`` can
decide what's permanent vs ephemeral at session end.

The scratchpad is ephemeral by default — it lives in process memory
and dies with the session. Persistence happens via FAFMemory.etch()
or FAFMemory.merge(), each calling write_soul on MCPaaS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScratchpadEntry:
    """A single scratchpad value with merge-time metadata.

    Attributes
    ----------
    value : str
        The actual content.
    priority : str
        One of ``"high"``, ``"medium"`` (default), ``"low"``,
        ``"ephemeral"``. The default heuristic merge promotes
        everything except ``"ephemeral"``.
    tag : str | None
        Smart-tag describing what kind of fact this is (e.g. ``"address"``,
        ``"preference"``, ``"name"``). Free-form today; will become
        user-definable in a future gate. ``None`` if no tag.
    """

    value: str
    priority: str = "medium"
    tag: str | None = None


class Scratchpad:
    """In-memory key/value store for the current voice session.

    Backward-compatible with the Gate 2 API (``update(key, value)``,
    ``get(key)``). Gate 4 adds optional ``priority`` / ``tag`` kwargs
    on update, plus ``get_entry`` / ``all_entries`` for merge access.

    Examples
    --------
    >>> pad = Scratchpad()
    >>> pad.update("address", "123 Main St", priority="high", tag="contact")
    >>> pad.get("address")
    '123 Main St'
    >>> pad.get_entry("address").priority
    'high'
    """

    def __init__(self) -> None:
        self._data: dict[str, ScratchpadEntry] = {}

    def update(
        self,
        key: str,
        value: str,
        *,
        priority: str = "medium",
        tag: str | None = None,
    ) -> None:
        """Set a key in the scratchpad with optional metadata."""
        self._data[key] = ScratchpadEntry(value=value, priority=priority, tag=tag)

    def get(self, key: str) -> str | None:
        """Get the VALUE of a key. Returns None if missing."""
        entry = self._data.get(key)
        return entry.value if entry else None

    def get_entry(self, key: str) -> ScratchpadEntry | None:
        """Get the full entry (value + priority + tag)."""
        return self._data.get(key)

    def all(self) -> dict[str, str]:
        """Return a copy of the scratchpad as {key: value}."""
        return {k: e.value for k, e in self._data.items()}

    def all_entries(self) -> dict[str, ScratchpadEntry]:
        """Return a copy of the scratchpad as {key: ScratchpadEntry}."""
        return dict(self._data)

    def clear(self) -> None:
        """Empty the scratchpad."""
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data
