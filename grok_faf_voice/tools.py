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
the agent runs (FAFMemory's own five AND any user-defined tools).
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

    Used by the FAFMemory factories to opt out of generic
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
    """Return an @function_tool that stages a fact. It does not write."""
    _ = bus or mem.bus  # reserved for future per-factory emissions

    @function_tool
    async def etch_memory(context: RunContext, content: str) -> str:
        """Stage a fact. This does not save it.

        ONLY CALL THIS TOOL WHEN YOU HAVE THE ACTUAL FACT TO REMEMBER.
        The ``content`` argument MUST be the substance itself — never
        the user's meta-statement about wanting to remember something.

        After this returns, say the exact sentence it gives you, then
        wait. Call confirm_memory only after the namepoint holder
        agrees. A second voice in the room is not a confirmation.

        EXAMPLES (correct):
          User: "Remember my favorite color is cyan."
          → etch_memory(content="favorite color is cyan")
          → say exactly: "I'll remember this: favorite color is cyan."
          → wait for the holder to confirm, then confirm_memory.

        EXAMPLES (DO NOT do this):
          User: "I want to make a memory."
          → DO NOT CALL THIS TOOL. Reply: "Sure, what would you like me
            to remember?" Wait for the actual content.

        CRITICAL LATENCY RULE (must follow):
        - Speak a short bridge FIRST ("Got it."), then call this tool.
        - Then say the exact sentence this tool returns.
        - Never go silent during tool execution.
        """
        _ = context
        spoken = mem.stage_memory(content, type="memory")
        return (
            "Staged, not saved. Say this exact sentence, then wait for "
            f"the namepoint holder to confirm: {spoken}"
        )

    return _mark_bus_wrapped(etch_memory)


def make_confirm_tool(mem: FAFMemory, bus: ContextBus | None = None):
    """Return an @function_tool that writes only after speak + confirm.

    ``holder_confirmed`` is not a parameter. The tool reads the
    transcript. A call with no transcript does not write.
    """
    from grok_faf_voice.memory import FAFAuthRequiredError, FAFEtchError

    bus = bus or mem.bus

    @function_tool
    async def confirm_memory(context: RunContext, content: str) -> str:
        """Save a staged fact after the holder confirms it.

        Call this only after you have spoken the exact sentence
        "I'll remember this: <fact>." and the namepoint holder has
        agreed. If you call this on your own, nothing is saved.

        ``content`` must be the staged fact, unchanged.
        """
        pending = mem.pending
        confirmed = holder_confirmed(
            _messages_from(getattr(context, "session", None)),
            pending["spoken"] if pending else "",
        )
        try:
            result = await mem.commit_staged(
                content, holder_confirmed=confirmed
            )
        except FAFAuthRequiredError as e:
            return str(e)
        except FAFEtchError as e:
            return f"Not saved: {e}"
        if pending and pending.get("type") == "paralinguistic":
            await bus.emit_paralinguistic_detected(
                marker_type=str(pending.get("marker_type") or ""),
                value=str(pending.get("marker_value") or content),
                topic=pending.get("topic"),
            )
        return result

    return _mark_bus_wrapped(confirm_memory)


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
        observable. This stages the marker. It is saved only after you
        speak the exact sentence and the namepoint holder confirms.

        CRITICAL LATENCY RULE (must follow):
        - Speak a short micro-bridge FIRST ("Noted the tone."), then
          call this tool. Then say the exact sentence it returns.
        - Never go silent.
        """
        _ = context
        entry, tags = mem.format_paralinguistic_entry(
            marker_type, value, context=topic
        )
        spoken = mem.stage_memory(entry, type="paralinguistic", tags=tags)
        if mem.pending is not None:
            mem.pending["marker_type"] = marker_type
            mem.pending["marker_value"] = value
            mem.pending["topic"] = topic
        return (
            "Staged, not saved. Say this exact sentence, then wait for "
            f"the namepoint holder to confirm: {spoken}"
        )

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


_YES = {
    "yes",
    "yeah",
    "yep",
    "yup",
    "confirm",
    "confirmed",
    "ok",
    "okay",
    "sure",
    "please",
    "do it",
    "go ahead",
    "yes please",
    "yeah please",
    "ok please",
}


def _role_of(message: Any) -> str:
    role = message.get("role") if isinstance(message, dict) else getattr(message, "role", "")
    value = getattr(role, "value", role)
    return str(value).lower()


def _text_of(message: Any) -> str:
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(
                    str(getattr(part, "text", "") or getattr(part, "content", "") or "")
                )
        return " ".join(part for part in parts if part)
    return str(content or "")


def _messages_from(session: Any) -> list[Any]:
    """Transcript items, or an empty list when the session has none."""
    if session is None:
        return []
    history = getattr(session, "history", None)
    if history is None:
        history = getattr(session, "chat_ctx", None)
    if history is None:
        return []
    if isinstance(history, list):
        return history
    messages = getattr(history, "messages", None)
    if callable(messages):
        try:
            return list(messages())
        except TypeError:
            return []
    items = getattr(history, "items", None)
    if items is not None:
        return list(items)
    return []


def _is_yes(text: str) -> bool:
    cleaned = text.strip().lower().strip(" .!?,")
    return cleaned in _YES


def holder_confirmed(messages: list[Any], spoken: str) -> bool:
    """True when the holder said yes after the agent spoke ``spoken``.

    A missing transcript is not a confirmation. The model's own call
    cannot set this true.
    """
    if not messages or not spoken:
        return False
    last_user = None
    for index, message in enumerate(messages):
        if _role_of(message) == "user":
            last_user = index
    if last_user is None or not _is_yes(_text_of(messages[last_user])):
        return False
    for message in messages[:last_user]:
        role = _role_of(message)
        if role in {"assistant", "agent"} and spoken in _text_of(message):
            return True
    return False


# ----------------------------------------------------------------
# Global tool bus middleware
#
# Gives bus coverage to EVERY tool the agent runs (FAFMemory + any
# user-defined). Mutates each FunctionTool in place by swapping its
# underlying callable with a wrapper that emits tool.about_to_run
# before and tool.completed after. Idempotent via _bus_wrapped
# sentinel — already-wrapped tools (including the FAFMemory
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
