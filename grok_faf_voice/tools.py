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

import asyncio
import logging
import re
from functools import wraps
from typing import TYPE_CHECKING, Any

from livekit.agents import RunContext, function_tool

from grok_faf_voice.context_bus import BusEvent, ContextBus

log = logging.getLogger(__name__)

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
        stop. Do not call another tool to save it. The SDK saves when
        the namepoint holder's next final reply is a yes. A second
        voice in the room is not a confirmation.

        EXAMPLES (correct):
          User: "Remember my favorite color is cyan."
          → etch_memory(content="favorite color is cyan")
          → say exactly: "I'll remember this: favorite color is cyan."
          → stop. The holder confirms by speaking.

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
            "Staged, not saved. Say this exact sentence, then stop: "
            f"{spoken}"
        )

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
        observable. This stages the marker. Say the exact sentence it
        returns, then stop. The SDK saves when the holder agrees.

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
            "Staged, not saved. Say this exact sentence, then stop: "
            f"{spoken}"
        )

    return _mark_bus_wrapped(note_paralinguistic)


def make_merge_tool(
    mem: FAFMemory,
    session: AgentSession,
    bus: ContextBus | None = None,
):
    """Return an @function_tool that stages a scratchpad save.

    The model says ``SAVE_SPOKEN`` and stops. The SDK listener writes
    when the holder's next final reply is a yes and that sentence ends
    the last assistant message. Saying the sentence and then hearing
    yes also saves, which is the live order. Session-only scratchpad
    entries are not promoted. This tool does not read the transcript.
    """
    _ = bus or mem.bus
    _ = session

    @function_tool
    async def merge_now(context: RunContext) -> str:
        """Stage a save of what we discussed. This does not write.

        Say exactly "I'll save what we discussed." and then stop.
        The SDK saves when the namepoint holder's next final reply is
        a yes. A yes to a later question does not count. Session-only
        facts are not saved.

        CRITICAL LATENCY RULE (multi-second work):
        - Speak the sentence, then stop. After the holder agrees, the
          SDK says "Give me just a moment while I consolidate
          everything..." before the merge runs, and a short
          confirmation after.
        """
        _ = context
        already = mem.saved_merge_result()
        if already:
            return already
        if mem.pending is not None:
            return (
                "A fact is waiting for the holder to confirm. "
                "Nothing else was saved."
            )
        mem.arm_merge()
        return (
            "Staged, not saved. Say this exact sentence, then stop: "
            f"{SAVE_SPOKEN}"
        )

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


def _fold_apostrophes(text: str) -> str:
    return (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


def _norm_match(text: str) -> str:
    """Lower-case, drop punctuation, collapse whitespace."""
    folded = _fold_apostrophes(text).casefold()
    folded = re.sub(r"[^\w\s]", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


def _has_refusal(text: str) -> bool:
    """True when the reply contains ``no`` or ``don't`` as words."""
    folded = _fold_apostrophes(text).casefold()
    return re.search(r"\b(?:no|don'?t|do not)\b", folded) is not None


def _is_yes(text: str) -> bool:
    """A yes-prefix, unless the reply contains ``no`` or ``don't``.

    ``Yes.`` and ``Yes, please do.`` both count. ``yes don't`` does not.
    """
    if _has_refusal(text):
        return False
    folded = _norm_match(text)
    if not folded:
        return False
    for token in sorted(_YES, key=len, reverse=True):
        head = _norm_match(token)
        if folded == head or folded.startswith(head + " "):
            return True
    return False


def _ends_with_spoken(text: str, spoken: str) -> bool:
    """True when ``spoken`` ends the message, after normalisation.

    Punctuation, case, and whitespace are folded on both sides, so a
    comma and a colon compare equal. The sentence still has to be the
    end of the assistant message. A bridge may come first. Words after
    the sentence do not confirm.
    """
    body = _norm_match(text)
    sentence = _norm_match(spoken)
    if not sentence:
        return False
    if body == sentence:
        return True
    if not body.endswith(sentence):
        return False
    prefix = body[: -len(sentence)]
    return prefix.endswith(" ")


# The sentence merge_now requires before it writes.
SAVE_SPOKEN = "I'll save what we discussed."


def holder_confirmed(messages: list[Any], spoken: str) -> bool:
    """True when the holder's latest line is a yes to ``spoken``.

    The last assistant message before that yes must end with
    ``spoken``. A bridge such as ``Got it.`` may come first. A
    question after the sentence does not count. A yes to a later
    turn does not confirm an older sentence. A missing transcript
    is not a confirmation. Any user turn confirms. The second-voice
    rule is the prompt plus the operator flags.
    """
    if not messages or not spoken:
        return False
    last_user = None
    for index, message in enumerate(messages):
        if _role_of(message) == "user":
            last_user = index
    if last_user is None or not _is_yes(_text_of(messages[last_user])):
        return False
    previous = None
    for message in reversed(messages[:last_user]):
        if _role_of(message) in {"assistant", "agent"}:
            previous = message
            break
    if previous is None:
        return False
    return _ends_with_spoken(_text_of(previous), spoken)


def _last_assistant_text(messages: list[Any]) -> str:
    """Text of the latest assistant message, or an empty string."""
    for message in reversed(messages):
        if _role_of(message) in {"assistant", "agent"}:
            return _text_of(message)
    return ""


def _subscribe(session: Any, event: str, callback: Any) -> None:
    """Register ``callback`` on a LiveKit session, or a decorator stand-in."""
    on = getattr(session, "on", None)
    if not callable(on):
        return
    try:
        on(event, callback)
    except TypeError:
        on(event)(callback)


class HolderConfirm:
    """Save a staged fact from the holder's next final transcript.

    The model only stages. This listener is the confirm. Attach it with
    :meth:`FAFMemory.attach_confirm`. Interim transcripts do nothing.
    A final yes saves only when the last assistant message ends with
    the staged sentence. Any other final reply drops the stage.
    """

    def __init__(self, mem: FAFMemory, session: Any | None = None) -> None:
        self._mem = mem
        self._session = session
        self._attached = False
        self._heard_assistant = ""

    @property
    def session(self) -> Any | None:
        return self._session

    def attach(self) -> None:
        """Listen for ``user_input_transcribed``. Idempotent."""
        if self._attached or self._session is None:
            return
        _subscribe(self._session, "user_input_transcribed", self._on_user_input)
        _subscribe(self._session, "conversation_item_added", self._on_item)
        self._attached = True

    def _on_item(self, ev: Any) -> None:
        item = getattr(ev, "item", None)
        if item is None:
            return
        if _role_of(item) not in {"assistant", "agent"}:
            return
        text = _text_of(item)
        if text.strip():
            self._heard_assistant = text

    def _on_user_input(self, ev: Any) -> None:
        """Final transcripts only. Interim replies do not save or drop."""
        if not getattr(ev, "is_final", False):
            return
        transcript = str(getattr(ev, "transcript", "") or "")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.apply_final(transcript))
        task.add_done_callback(self._log_task)

    def _log_task(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning("holder confirm failed: %s", exc)

    def _assistant_text(self, messages: list[Any] | None) -> str:
        msgs = messages if messages is not None else _messages_from(self._session)
        text = _last_assistant_text(msgs)
        if text.strip():
            return text
        return self._heard_assistant

    async def _say(self, text: str) -> None:
        say = getattr(self._session, "say", None)
        if not callable(say):
            return
        await say(text)

    async def apply_final(
        self,
        transcript: str,
        *,
        messages: list[Any] | None = None,
    ) -> str:
        """Decide the staged fact, or a scratchpad save, from one final line.

        ``transcript`` is the final user-input-transcribed text. It is
        not read from session history. The last assistant message is.
        """
        if self._mem.pending is not None:
            return await self._resolve_fact(transcript, messages)
        return await self._resolve_merge(transcript, messages)

    async def _resolve_fact(
        self,
        transcript: str,
        messages: list[Any] | None,
    ) -> str:
        pending = self._mem.pending
        if pending is None:
            return ""
        assistant = self._assistant_text(messages)
        if _is_yes(transcript) and _ends_with_spoken(assistant, pending["spoken"]):
            return await self._commit(pending)
        self._mem.drop_pending()
        log.info("holder confirm: dropped")
        await self._say("Nothing was saved.")
        return self._mem.note_confirm_result("Nothing was saved.")

    async def _commit(self, pending: dict[str, Any]) -> str:
        from grok_faf_voice.memory import FAFAuthRequiredError, FAFEtchError

        try:
            result = await self._mem.commit_staged(
                pending["content"], holder_confirmed=True
            )
        except FAFAuthRequiredError as exc:
            return self._mem.note_confirm_result(str(exc))
        except FAFEtchError as exc:
            return self._mem.note_confirm_result(f"Not saved: {exc}")
        if pending.get("type") == "paralinguistic":
            await self._mem.bus.emit_paralinguistic_detected(
                marker_type=str(pending.get("marker_type") or ""),
                value=str(pending.get("marker_value") or pending["content"]),
                topic=pending.get("topic"),
            )
        log.info("holder confirm: saved")
        return self._mem.note_confirm_result(result)

    async def _resolve_merge(
        self,
        transcript: str,
        messages: list[Any] | None,
    ) -> str:
        assistant = self._assistant_text(messages)
        armed = self._mem.merge_armed
        matches = _is_yes(transcript) and _ends_with_spoken(assistant, SAVE_SPOKEN)
        if matches:
            self._mem.clear_merge_arm()
            return await self._run_merge()
        if armed:
            self._mem.clear_merge_arm()
            log.info("holder confirm: dropped")
            await self._say("Nothing was saved.")
            return self._mem.note_confirm_result("Nothing was saved.")
        return ""

    async def _run_merge(self) -> str:
        from grok_faf_voice.memory import (
            FAFAuthRequiredError,
            FAFEtchError,
            FAFMergeError,
        )

        try:
            await self._say(
                "Give me just a moment while I consolidate everything..."
            )
            await self._mem.bus.emit_merge_starting("user-initiated merge_now")
            result: dict[str, Any] = await self._mem.merge(strategy="grok-decides")
            await self._mem.bus.emit_merge_completed(result=result)
            promoted = result.get("promoted", 0)
            merged = result.get("merged", 0)
            total = promoted + merged
            if total > 0:
                await self._say(f"All set — saved {total} items.")
            else:
                await self._say("All tidy — nothing new to save this time.")
            text = (
                f"Saved {promoted} memories. "
                f"{result.get('kept_ephemeral', result.get('discarded', 0))} "
                "kept ephemeral."
            )
        except FAFAuthRequiredError as exc:
            return self._mem.note_confirm_result(str(exc))
        except (FAFEtchError, FAFMergeError) as exc:
            return self._mem.note_confirm_result(f"Could not save: {exc}")
        log.info("holder confirm: saved")
        return self._mem.note_confirm_result(self._mem.remember_merge_result(text))


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
