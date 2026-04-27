"""Scratchpad — in-memory store for the current voice session.

Gate 2 surface: simple key/value store + manual update API. No
auto-extraction yet — voice commands trigger explicit updates via
the @function_tool wrappers in tools.py.

Gate 4 will add Smart Merge (scratchpad → permanent .faf at session
end). Gate 6 will add the Real-time Context Bus that injects
scratchpad state into LiveKit sessions at sub-80ms.

The scratchpad is ephemeral by default — it lives in process memory
and dies with the session. Persistence happens via FAFMemory.etch()
calling write_soul on MCPaaS.
"""

from __future__ import annotations


class Scratchpad:
    """In-memory key/value store for the current voice session.

    Examples
    --------
    >>> pad = Scratchpad()
    >>> pad.update("address", "123 Main St")
    >>> pad.get("address")
    '123 Main St'
    >>> pad.all()
    {'address': '123 Main St'}
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def update(self, key: str, value: str) -> None:
        """Set a key in the scratchpad."""
        self._data[key] = value

    def get(self, key: str) -> str | None:
        """Get a key from the scratchpad. Returns None if missing."""
        return self._data.get(key)

    def all(self) -> dict[str, str]:
        """Return a copy of the entire scratchpad state."""
        return dict(self._data)

    def clear(self) -> None:
        """Empty the scratchpad."""
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data
