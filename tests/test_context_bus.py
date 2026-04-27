"""Tests for the Context Bus — async pub/sub for voice-memory events."""

from __future__ import annotations

import asyncio

import pytest

from grok_faf_voice import (
    BusEvent,
    BusEventPayload,
    ContextBus,
    FAFMemory,
)

# ----------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------


async def test_bus_start_stop_idempotent():
    """start() and stop() are both idempotent and don't crash."""
    bus = ContextBus()
    assert bus.running is False
    await bus.start()
    assert bus.running is True
    await bus.start()  # second start no-ops
    assert bus.running is True
    await bus.stop()
    assert bus.running is False
    await bus.stop()  # second stop no-ops
    assert bus.running is False


async def test_emit_auto_starts_bus():
    """A first emit() before any explicit start() auto-starts the bus."""
    bus = ContextBus()
    await bus.emit(BusEvent.SCRATCHPAD_UPDATED, {"k": "v"})
    assert bus.running is True
    await bus.stop()


# ----------------------------------------------------------------
# Async subscription
# ----------------------------------------------------------------


async def test_async_handler_receives_event():
    """An async handler registered via on() receives published events."""
    bus = ContextBus()
    received: list[BusEventPayload] = []

    async def handler(payload: BusEventPayload) -> None:
        received.append(payload)

    bus.on(BusEvent.SCRATCHPAD_UPDATED, handler)
    await bus.emit(BusEvent.SCRATCHPAD_UPDATED, {"k": "v"})
    await asyncio.sleep(0.05)  # let dispatcher drain

    assert len(received) == 1
    assert received[0].event == BusEvent.SCRATCHPAD_UPDATED
    assert received[0].payload == {"k": "v"}
    assert received[0].source == "voice_memory_layer"

    await bus.stop()


async def test_decorator_registration():
    """`@bus.on(event)` decorator-style registration works."""
    bus = ContextBus()
    received: list[BusEventPayload] = []

    @bus.on(BusEvent.SOUL_UPDATED)
    async def _handler(payload: BusEventPayload) -> None:
        received.append(payload)

    await bus.emit(BusEvent.SOUL_UPDATED, {"soul": "grok"})
    await asyncio.sleep(0.05)

    assert len(received) == 1
    await bus.stop()


async def test_multiple_handlers_all_fire():
    """All registered handlers for an event fire on each emit."""
    bus = ContextBus()
    counts = [0, 0, 0]

    async def make(idx: int):
        async def h(payload: BusEventPayload) -> None:
            counts[idx] += 1

        return h

    bus.on(BusEvent.MERGE_STARTING, await make(0))
    bus.on(BusEvent.MERGE_STARTING, await make(1))
    bus.on(BusEvent.MERGE_STARTING, await make(2))

    await bus.emit(BusEvent.MERGE_STARTING, {"reason": "test"})
    await asyncio.sleep(0.05)

    assert counts == [1, 1, 1]
    await bus.stop()


async def test_event_isolation():
    """Handler for event A doesn't fire on emit of event B."""
    bus = ContextBus()
    a_count = 0
    b_count = 0

    async def handle_a(payload: BusEventPayload) -> None:
        nonlocal a_count
        a_count += 1

    async def handle_b(payload: BusEventPayload) -> None:
        nonlocal b_count
        b_count += 1

    bus.on(BusEvent.SCRATCHPAD_UPDATED, handle_a)
    bus.on(BusEvent.SOUL_UPDATED, handle_b)

    await bus.emit(BusEvent.SCRATCHPAD_UPDATED, {})
    await asyncio.sleep(0.05)

    assert a_count == 1
    assert b_count == 0
    await bus.stop()


# ----------------------------------------------------------------
# Sync compat
# ----------------------------------------------------------------


async def test_on_sync_wraps_handler():
    """on_sync() wraps a sync handler and dispatches it like async."""
    bus = ContextBus()
    received: list[BusEventPayload] = []

    def sync_handler(payload: BusEventPayload) -> None:
        received.append(payload)

    bus.on_sync(BusEvent.PARALINGUISTIC_DETECTED, sync_handler)
    await bus.emit(BusEvent.PARALINGUISTIC_DETECTED, {"tone": "calm"})
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].payload == {"tone": "calm"}
    await bus.stop()


async def test_off_removes_handler():
    """off() removes a previously-registered handler."""
    bus = ContextBus()
    received: list[BusEventPayload] = []

    async def handler(payload: BusEventPayload) -> None:
        received.append(payload)

    bus.on(BusEvent.SCRATCHPAD_UPDATED, handler)
    await bus.emit(BusEvent.SCRATCHPAD_UPDATED, {})
    await asyncio.sleep(0.05)
    assert len(received) == 1

    bus.off(BusEvent.SCRATCHPAD_UPDATED, handler)
    await bus.emit(BusEvent.SCRATCHPAD_UPDATED, {})
    await asyncio.sleep(0.05)
    assert len(received) == 1, "off() should have unregistered the handler"

    await bus.stop()


# ----------------------------------------------------------------
# Error isolation
# ----------------------------------------------------------------


async def test_handler_exception_does_not_block_other_handlers(caplog):
    """A raising handler is logged but doesn't prevent other handlers
    from running on the same event.
    """
    bus = ContextBus()
    second_called = False

    async def raising(payload: BusEventPayload) -> None:
        raise RuntimeError("boom")

    async def second(payload: BusEventPayload) -> None:
        nonlocal second_called
        second_called = True

    bus.on(BusEvent.MERGE_PENDING, raising)
    bus.on(BusEvent.MERGE_PENDING, second)

    with caplog.at_level("WARNING"):
        await bus.emit(BusEvent.MERGE_PENDING, {})
        await asyncio.sleep(0.05)

    assert second_called is True
    assert any("boom" in rec.message for rec in caplog.records)
    await bus.stop()


# ----------------------------------------------------------------
# Backpressure
# ----------------------------------------------------------------


async def test_full_queue_drops_with_warning(caplog):
    """When the queue is full, excess emits are dropped with a warning,
    never blocking the caller.
    """
    bus = ContextBus(max_queue_size=2)

    # Block the dispatcher with a slow handler so the queue fills up.
    async def slow(payload: BusEventPayload) -> None:
        await asyncio.sleep(0.5)

    bus.on(BusEvent.AUDIO_CUE, slow)
    await bus.start()

    with caplog.at_level("WARNING"):
        # Fire 1 (taken by the dispatcher to feed `slow`), then 2 more
        # to fill the queue, then a 4th that should be dropped.
        for _ in range(4):
            await bus.emit(BusEvent.AUDIO_CUE, {})
        # Don't wait for slow to finish — we want to observe the drop.
        await asyncio.sleep(0.01)

    assert any("queue full" in rec.message.lower() for rec in caplog.records)
    await bus.stop()


# ----------------------------------------------------------------
# FAFMemory.bus exposure
# ----------------------------------------------------------------


def test_fafmemory_owns_a_bus():
    """FAFMemory exposes its bus via the .bus property."""
    mem = FAFMemory("grok", token="t")
    assert isinstance(mem.bus, ContextBus)


def test_fafmemory_bus_is_injectable():
    """A bus passed via constructor is honored (for tests / advanced use)."""
    bus = ContextBus()
    mem = FAFMemory("grok", token="t", bus=bus)
    assert mem.bus is bus


async def test_fafmemory_start_stop_bus_methods():
    """mem.start_bus() and mem.stop_bus() drive the bus lifecycle."""
    mem = FAFMemory("grok", token="t")
    assert mem.bus.running is False
    await mem.start_bus()
    assert mem.bus.running is True
    await mem.stop_bus()
    assert mem.bus.running is False


# ----------------------------------------------------------------
# Convenience emitters
# ----------------------------------------------------------------


@pytest.mark.parametrize(
    "method_name,event,extra",
    [
        ("emit_scratchpad_updated", BusEvent.SCRATCHPAD_UPDATED, {"k": "v"}),
        ("emit_merge_starting", BusEvent.MERGE_STARTING, {}),
        ("emit_paralinguistic_detected", BusEvent.PARALINGUISTIC_DETECTED, {}),
    ],
)
async def test_convenience_emitters(method_name, event, extra):
    """Each convenience method publishes the right event type."""
    bus = ContextBus()
    received: list[BusEventPayload] = []

    async def h(payload: BusEventPayload) -> None:
        received.append(payload)

    bus.on(event, h)
    await getattr(bus, method_name)(**extra)
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].event == event
    await bus.stop()


async def test_emit_tool_about_to_run_payload_shape():
    """emit_tool_about_to_run carries tool name + args."""
    bus = ContextBus()
    received: list[BusEventPayload] = []

    async def h(payload: BusEventPayload) -> None:
        received.append(payload)

    bus.on(BusEvent.TOOL_ABOUT_TO_RUN, h)
    await bus.emit_tool_about_to_run("etch_memory", {"content": "hi"})
    await asyncio.sleep(0.05)

    assert received[0].payload == {
        "tool": "etch_memory",
        "args": {"content": "hi"},
    }
    await bus.stop()
