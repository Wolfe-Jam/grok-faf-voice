"""LiveKit @function_tool wrappers for FAFMemory.

The voice agent triggers these naturally when the user says something
like "etch this..." or "what do you remember?". The decorator surfaces
them as callable tools to the realtime model.

Each tool docstring includes a CRITICAL LATENCY RULE block — the
realtime stream goes silent during tool execution, so the model is
instructed to speak a short verbal bridge before calling. Fast tools
(300-700ms) use a one-word bridge ("Got it.", "Noted."); the heavier
``merge_now`` (multi-second) uses an explicit ``session.say()``
foreground hold for full UX coverage.

Generic ``tool.about_to_run`` and ``tool.completed`` events come from
``enable_global_tool_bus(memory, agent)`` — call it once after the
Agent is constructed to give the Bus full visibility over EVERY tool
the agent runs (FAFMemory's own four AND any user-defined tools).
The factory output is marked ``_bus_wrapped=True`` so the global
wrapper skips re-wrapping; only domain-specific events
(``soul.updated``, ``paralinguistic.detected``, ``merge.starting``,
``merge.completed``) are emitted from inside the factory bodies or
from FAFMemory itself.

Use via `FAFMemory.tools(session)`:

    mem = FAFMemory("grok", api_key=...)
    session = AgentSession(llm=xai.realtime.RealtimeModel(voice="Ara"))
    agent = Agent(instructions=..., tools=mem.tools(session))
    enable_global_tool_bus(mem, agent)  # gives bus coverage to every tool
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any

from livekit.agents import RunContext, function_tool

from grok_faf_voice.context_bus import BusEvent, ContextBus

if TYPE_CHECKING:
    from livekit.agents import Agent, AgentSession

    from grok_faf_voice.memory import FAFMemory


def _mark_bus_wrapped(tool: Any) -> Any:
    """Tag a FunctionTool so ``_wrap_tool_with_bus`` skips re-wrapping.

    Used by the four FAFMemory factories to opt out of generic
    ``tool.*`` wrapping (they emit only domain-specific events; the
    global wrapper handles uniform tool observability everywhere else).
    """
    try:
        tool._bus_wrapped = True
    except AttributeError:
        # If the FunctionTool implementation tightens slots in a future
        # version and this raises, the global wrapper would silently
        # double-emit. Surface that early during apply so we adapt.
        raise
    return tool


def make_etch_tool(mem: FAFMemory, bus: ContextBus | None = None):
    """Return an @function_tool that etches voice content to FAFMemory."""
    from grok_faf_voice.memory import FAFAuthRequiredError, FAFEtchError

    _ = bus or mem.bus  # reserved for future per-factory emissions

    @function_tool
    async def etch_memory(context: RunContext, content: str) -> str:
        """Save content to persistent eternal memory.

        ONLY CALL THIS TOOL WHEN YOU HAVE THE ACTUAL FACT TO REMEMBER.
        The ``content`` argument MUST be the substance itself — never
        the user's meta-statement about wanting to remember something.

        EXAMPLES (correct):
          User: "Remember my favorite color is cyan."
          → etch_memory(content="favorite color is cyan")

          User: "Etch this — today is launch day for v0.1.3."
          → etch_memory(content="today is launch day for v0.1.3")

        EXAMPLES (DO NOT do this):
          User: "I want to make a memory."
          → DO NOT CALL THIS TOOL. The user has stated intent but no
            substance. Reply: "Sure, what would you like me to
            remember?" Wait for the actual content, then call
            etch_memory with ONLY the content (not the user's intent).

          User: "Make a note."
          → DO NOT CALL THIS TOOL. Reply: "Of course, what should I
            note?" Wait, then etch the substance.

        Etching meta-statements ("user wants to make a memory") pollutes
        the soul with non-content. When in doubt, ASK and WAIT.

        CRITICAL LATENCY RULE (must follow):
        - ALWAYS speak a short natural bridge FIRST (under 400ms):
          "Got it.", "Noted.", "I'll remember that." — THEN call this tool.
        - Never go silent. The realtime stream emits no filler audio
          during tool execution.
        """
        # tool.about_to_run / tool.completed come from the global
        # wrapper (enable_global_tool_bus). Domain-specific
        # ``soul.updated`` is emitted by ``mem.etch()`` itself.
        try:
            await mem.etch(content, type="memory")
            return f"Etched: {content}"
        except FAFAuthRequiredError as e:
            return str(e)
        except FAFEtchError as e:
            return f"Could not save: {e}"

    return _mark_bus_wrapped(etch_memory)


def make_recall_tool(mem: FAFMemory, bus: ContextBus | None = None):
    """Return an @function_tool that reads the current soul state."""
    from grok_faf_voice.memory import FAFRecallError

    _ = bus or mem.bus  # reserved for future per-factory emissions

    @function_tool
    async def recall_memory(context: RunContext) -> str:
        """Retrieve the current persistent memory state.

        Use when the user asks "what do you remember", "what did
        I say last time", or otherwise wants to surface what's in
        the soul. Returns the full soul body.

        CRITICAL LATENCY RULE (must follow):
        - ALWAYS speak a short natural bridge FIRST: "Let me check...",
          "Pulling that up...", "One moment while I recall...", or
          "Looking that up for you." — THEN call this tool.
        - Never go silent.
        """
        # tool.about_to_run / tool.completed come from the global
        # wrapper. No domain-specific event for recall.
        try:
            return await mem.get()
        except FAFRecallError as e:
            return f"Could not recall: {e}"

    return _mark_bus_wrapped(recall_memory)


def make_paralinguistic_tool(mem: FAFMemory, bus: ContextBus | None = None):
    """Return an @function_tool that records paralinguistic markers.

    The model decides when the user's tone/style/emotional state is
    worth recording — typically when:
      - The user sounds notably frustrated, excited, calm, urgent
      - The user's speaking pace shifts (faster when stressed, etc.)
      - The user interrupts frequently or pauses notably
    """
    from grok_faf_voice.memory import FAFAuthRequiredError, FAFEtchError

    bus = bus or mem.bus

    @function_tool
    async def note_paralinguistic(
        context: RunContext,
        marker_type: str,
        value: str,
        topic: str | None = None,
    ) -> str:
        """Record HOW the user is speaking (tone, emotional state, style).

        Call when the user's voice tone, emotional state, or speaking
        style is worth remembering for future sessions. Examples:

          marker_type="tone", value="frustrated", topic="checkout bug"
          marker_type="emotional_state", value="excited", topic="launch"
          marker_type="speaking_style", value="rapid", topic="under pressure"
          marker_type="interruption_pattern", value="frequent"

        Don't fabricate markers. Only call when the cue is genuinely
        observable. Markers persist across sessions so the agent can
        open future calls with appropriate awareness.

        CRITICAL LATENCY RULE (must follow):
        - ALWAYS speak a short micro-bridge FIRST: "Noted the tone.",
          "Got the vibe.", "Registering that.", or "Marking the energy."
          — THEN call this tool.
        - Never go silent.
        """
        # tool.about_to_run / tool.completed come from the global
        # wrapper. Domain-specific paralinguistic.detected fires here
        # on the success path so subscribers can react regardless of
        # whether enable_global_tool_bus was called.
        try:
            await mem.etch_paralinguistic(
                marker_type, value, context=topic
            )
            await bus.emit_paralinguistic_detected(
                marker_type=marker_type, value=value, topic=topic
            )
            return f"Noted: {marker_type}={value}"
        except FAFAuthRequiredError as e:
            return str(e)
        except FAFEtchError as e:
            return f"Could not note: {e}"

    return _mark_bus_wrapped(note_paralinguistic)


def make_merge_tool(
    mem: FAFMemory,
    session: AgentSession,
    bus: ContextBus | None = None,
):
    """Return an @function_tool that promotes scratchpad → soul.

    The model fires this when the user signals end-of-conversation
    with phrases like "save this", "commit our notes", "remember all
    of this". After firing, the scratchpad is cleared and important
    items are permanent in the soul.

    Pattern B (heavy tool): closes over ``session`` to emit an
    explicit verbal hold via ``session.say()`` before the multi-second
    merge runs, then a short confirmation after. Without this, the
    realtime stream goes silent for several seconds and the user
    perceives a stall.
    """
    from grok_faf_voice.memory import (
        FAFAuthRequiredError,
        FAFEtchError,
        FAFMergeError,
    )

    bus = bus or mem.bus

    @function_tool
    async def merge_now(context: RunContext) -> str:
        """Promote in-session scratchpad memories to permanent soul.

        Use when the user says "save this", "save what we discussed",
        "commit our notes", "remember all of this", or otherwise
        signals they want to lock in the conversation. Returns a
        count of what was kept and what was discarded.

        CRITICAL LATENCY RULE (multi-second tool):
        - This SDK emits an explicit verbal hold ("Give me just a
          moment while I consolidate everything...") before the merge
          runs and a short confirmation after. The model does not need
          to speak a bridge here — the SDK handles it.
        """
        # tool.about_to_run / tool.completed come from the global
        # wrapper. Domain-specific merge.starting + merge.completed
        # fire here so subscribers can observe user-initiated merges
        # even without enable_global_tool_bus.
        try:
            await session.say(
                "Give me just a moment while I consolidate everything..."
            )
            await bus.emit_merge_starting("user-initiated merge_now")
            result: dict[str, Any] = await mem.merge(strategy="grok-decides")
            await bus.emit_merge_completed(result=result)
            promoted = result.get("promoted", 0)
            merged = result.get("merged", 0)
            total = promoted + merged
            if total > 0:
                await session.say(f"All set — saved {total} items.")
            else:
                await session.say("All tidy — nothing new to save this time.")
            return (
                f"Saved {promoted} memories. "
                f"{result.get('kept_ephemeral', result.get('discarded', 0))} kept ephemeral."
            )
        except FAFAuthRequiredError as e:
            return str(e)
        except (FAFEtchError, FAFMergeError) as e:
            return f"Could not save: {e}"

    return _mark_bus_wrapped(merge_now)


# ----------------------------------------------------------------
# Global tool bus middleware
#
# Gives bus coverage to EVERY tool the agent runs (FAFMemory + any
# user-defined). Mutates each FunctionTool in place by swapping its
# underlying callable with a wrapper that emits tool.about_to_run
# before and tool.completed after. Idempotent via _bus_wrapped
# sentinel — already-wrapped tools (including the four FAFMemory
# factories) are skipped.
# ----------------------------------------------------------------


def _wrap_tool_with_bus(tool: Any, bus: ContextBus) -> Any:
    """Wrap any LiveKit FunctionTool with bus events. Idempotent.

    Mutates the tool in place by swapping its ``_func`` attribute
    with a wrapper that emits ``tool.about_to_run`` before invocation
    and ``tool.completed`` after (with ``success: True`` + ``result``
    on success, ``success: False`` + ``error`` on exception). The
    exception is re-raised so LiveKit's error handling is preserved.

    Tools tagged ``_bus_wrapped=True`` (e.g. FAFMemory factory output)
    are returned unchanged.
    """
    if getattr(tool, "_bus_wrapped", False):
        return tool

    original = tool._func
    tool_name = str(
        getattr(getattr(tool, "info", None), "name", None)
        or getattr(original, "__name__", "unknown_tool")
    )

    @wraps(original)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        await bus.emit_tool_about_to_run(tool_name, kwargs)
        try:
            result = await original(*args, **kwargs)
            await bus.emit(
                BusEvent.TOOL_COMPLETED,
                {"tool": tool_name, "result": result, "success": True},
            )
            return result
        except Exception as exc:
            await bus.emit(
                BusEvent.TOOL_COMPLETED,
                {"tool": tool_name, "error": str(exc), "success": False},
            )
            raise

    tool._func = wrapped
    tool._bus_wrapped = True
    return tool


def enable_global_tool_bus(memory: FAFMemory, agent: Agent) -> None:
    """Give Bus coverage to every tool the agent runs.

    Wraps every tool in ``agent.tools`` so its execution emits
    ``tool.about_to_run`` and ``tool.completed`` on the Bus. Also
    patches ``agent.update_tools`` so any tools added later (e.g. via
    runtime tool registration) get the same coverage.

    Idempotent — safe to call multiple times. Tools already tagged
    ``_bus_wrapped=True`` (FAFMemory factory output, or anything
    previously wrapped) are skipped.

    Call once after the Agent is constructed and before
    ``session.start(...)``::

        agent = Agent(instructions=..., tools=mem.tools(session))
        enable_global_tool_bus(mem, agent)
    """
    bus = memory.bus

    for tool in (agent.tools or []):
        _wrap_tool_with_bus(tool, bus)

    # Patch update_tools so future additions get wrapped too.
    if not getattr(agent, "_bus_update_tools_patched", False):
        original_update = agent.update_tools

        async def _wrapped_update_tools(tools: list[Any]) -> None:
            for tool in tools or []:
                _wrap_tool_with_bus(tool, bus)
            await original_update(tools)

        agent.update_tools = _wrapped_update_tools  # type: ignore[method-assign]
        agent._bus_update_tools_patched = True  # type: ignore[attr-defined]
