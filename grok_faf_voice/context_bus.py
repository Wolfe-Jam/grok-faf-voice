"""Context Bus — async pub/sub for voice-memory events.

The Bus gives developers precise, semantic control over the voice
memory layer: the right events at the right moments, with full async
power and backpressure. Built alongside LiveKit's `rtc.EventEmitter`
(not as a wrapper) so it can offer async-native subscriptions,
domain-specific event vocabulary, and lifecycle ownership that the
sync-only LiveKit emitter doesn't.

Public surface
--------------
- ``BusEvent`` — Enum of canonical event types
- ``BusEventPayload`` — Pydantic envelope (event + payload + timestamp + source)
- ``ContextBus`` — the bus itself

Usage
-----
::

    bus = ContextBus()
    await bus.start()

    @bus.on(BusEvent.SCRATCHPAD_UPDATED)
    async def handle(payload: BusEventPayload) -> None:
        print(payload.payload)

    await bus.emit(BusEvent.SCRATCHPAD_UPDATED, {"key": "x"})

For sync handlers (simple cases), use ``bus.on_sync(...)`` instead;
the bus wraps the callback in an async shim and dispatches it on the
same task lifecycle as native async handlers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------


class BusEvent(str, Enum):
    """Canonical event types published on the Context Bus.

    Inheriting from ``str`` keeps Python 3.10 compatibility while still
    giving each member a string value usable for serialization, logging,
    and external integration.
    """

    SCRATCHPAD_UPDATED = "scratchpad.updated"
    SCRATCHPAD_DIRTY = "scratchpad.dirty"
    SOUL_UPDATED = "soul.updated"
    MEMORY_SNAPSHOT = "memory.snapshot"
    PARALINGUISTIC_DETECTED = "paralinguistic.detected"
    TOOL_ABOUT_TO_RUN = "tool.about_to_run"
    TOOL_COMPLETED = "tool.completed"
    MERGE_PENDING = "merge.pending"
    MERGE_STARTING = "merge.starting"
    MERGE_COMPLETED = "merge.completed"
    SESSION_RESUMED = "session.resumed"
    CONTEXT_INVALIDATED = "context.invalidated"
    AUDIO_CUE = "audio.cue"


class BusEventPayload(BaseModel):
    """Envelope wrapping every event published on the bus.

    ``payload`` is free-form (callers shape it per event type). For
    structured shapes the SDK wants to standardize, callers can pass
    a Pydantic-validated dict via ``.model_dump()``.
    """

    event: BusEvent
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: str = "voice_memory_layer"

    model_config = {"arbitrary_types_allowed": True}


AsyncHandler = Callable[[BusEventPayload], Awaitable[None]]
SyncHandler = Callable[[BusEventPayload], None]


# ---------------------------------------------------------------------------
# Context Bus
# ---------------------------------------------------------------------------


class ContextBus:
    """Async-first pub/sub bus owned by a single ``FAFMemory`` instance.

    Single producer/consumer queue with a background dispatcher task.
    Backpressure: when the queue fills (default cap 1000), excess emits
    are dropped and a warning is logged — better than blocking the
    realtime path.

    Handler isolation: a raising handler is logged but doesn't stop the
    dispatcher or other handlers for the same event.

    Lifecycle: ``start()`` is idempotent and is auto-called on first
    ``emit()``. Always call ``stop()`` during shutdown to drain cleanly.
    """

    def __init__(self, max_queue_size: int = 1000) -> None:
        self._subscribers: dict[BusEvent, list[AsyncHandler]] = {}
        self._max_queue_size = max_queue_size
        self._queue: asyncio.Queue[BusEventPayload] | None = None
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def on(
        self,
        event: BusEvent,
        handler: AsyncHandler | None = None,
    ) -> Callable[[AsyncHandler], AsyncHandler] | AsyncHandler:
        """Register an async handler for ``event``.

        Usable as a method (``bus.on(event, fn)``) or as a decorator
        (``@bus.on(event)``).
        """

        def _register(fn: AsyncHandler) -> AsyncHandler:
            self._subscribers.setdefault(event, []).append(fn)
            return fn

        if handler is None:
            return _register
        return _register(handler)

    def on_sync(
        self,
        event: BusEvent,
        handler: SyncHandler,
    ) -> AsyncHandler:
        """Register a sync handler for ``event``.

        The bus wraps it in an async shim and dispatches it on the same
        task lifecycle as native async handlers. Returns the wrapper so
        callers can pass it back to ``off`` if needed.
        """

        async def _wrapper(payload: BusEventPayload) -> None:
            handler(payload)

        self._subscribers.setdefault(event, []).append(_wrapper)
        return _wrapper

    def off(self, event: BusEvent, handler: AsyncHandler) -> None:
        """Remove a previously-registered handler. Silent no-op if absent."""
        if event in self._subscribers:
            try:
                self._subscribers[event].remove(handler)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the dispatcher task. Idempotent."""
        if self._running:
            return
        self._queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._running = True
        self._task = asyncio.create_task(self._dispatcher())

    async def stop(self) -> None:
        """Stop the dispatcher task. Idempotent."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._queue = None

    @property
    def running(self) -> bool:
        """Whether the dispatcher task is active."""
        return self._running

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def emit(
        self,
        event: BusEvent,
        payload: dict[str, Any] | None = None,
        source: str = "voice_memory_layer",
    ) -> None:
        """Publish an event onto the bus.

        Auto-starts the bus on first emit. If the queue is full, the
        event is dropped and a warning logged — never blocks the caller.
        """
        if not self._running:
            await self.start()
        assert self._queue is not None  # narrows for type-checker

        envelope = BusEventPayload(
            event=event, payload=payload or {}, source=source
        )
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.warning(
                "ContextBus queue full (cap=%d) — dropping %s",
                self._max_queue_size,
                event.value,
            )

    # ------------------------------------------------------------------
    # Convenience emitters
    # ------------------------------------------------------------------

    async def emit_scratchpad_updated(self, **kwargs: Any) -> None:
        await self.emit(BusEvent.SCRATCHPAD_UPDATED, kwargs)

    async def emit_tool_about_to_run(
        self, tool_name: str, args: dict[str, Any] | None = None
    ) -> None:
        await self.emit(
            BusEvent.TOOL_ABOUT_TO_RUN,
            {"tool": tool_name, "args": args or {}},
        )

    async def emit_tool_completed(
        self,
        tool_name: str,
        result: Any | None = None,
        error: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"tool": tool_name}
        if result is not None:
            body["result"] = result
        if error is not None:
            body["error"] = error
        await self.emit(BusEvent.TOOL_COMPLETED, body)

    async def emit_merge_starting(self, reason: str = "") -> None:
        await self.emit(BusEvent.MERGE_STARTING, {"reason": reason})

    async def emit_merge_completed(
        self, result: dict[str, Any] | None = None
    ) -> None:
        await self.emit(BusEvent.MERGE_COMPLETED, {"result": result or {}})

    async def emit_paralinguistic_detected(self, **kwargs: Any) -> None:
        await self.emit(BusEvent.PARALINGUISTIC_DETECTED, kwargs)

    async def emit_session_resumed(
        self, summary: dict[str, Any] | None = None
    ) -> None:
        await self.emit(BusEvent.SESSION_RESUMED, summary or {})

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    async def _dispatcher(self) -> None:
        """Pull envelopes off the queue and fan out to handlers."""
        assert self._queue is not None
        while self._running:
            try:
                envelope = await self._queue.get()
            except asyncio.CancelledError:
                break

            for handler in list(self._subscribers.get(envelope.event, ())):
                try:
                    await handler(envelope)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ContextBus handler error on %s: %s",
                        envelope.event.value,
                        exc,
                    )
