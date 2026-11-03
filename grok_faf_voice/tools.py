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
          → invoke this tool with content "favorite color is cyan"
          → say only the sentence this tool returns
          → stop. Do not speak the tool call. The holder confirms.

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

        Call this tool, then say only the sentence it returns, and stop.
        Do not invent the sentence. The SDK saves when the namepoint
        holder's next final reply is a yes. A yes to a later question
        does not count. Session-only facts are not saved.

        CRITICAL LATENCY RULE (multi-second work):
        - Speak only the returned sentence, then stop. Do not speak the
          tool call. The SDK speaks while the save runs.
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

# Spoken once, to the model, when it stopped after a remember request
# without staging. Not a second parser: the model still calls the tool.
_ETCH_NUDGE = (
    "The holder asked you to remember a fact and you stopped without "
    "staging it. Invoke the etch_memory tool now with that fact. Then "
    "say only the sentence the tool returns. Do not speak the tool call. "
    "Do not say that it was saved."
)


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


def _last_user_text(messages: list[Any]) -> str:
    """Text of the latest user message, or an empty string."""
    for message in reversed(messages):
        if _role_of(message) == "user" and _text_of(message).strip():
            return _text_of(message)
    return ""


def _asks_to_remember(text: str) -> bool:
    """True when the holder asked to remember a fact, not only the verb."""
    if not re.search(r"\bremember\b", text, flags=re.IGNORECASE):
        return False
    return len(_norm_match(text).split()) >= 4


def _user_after_last_assistant(messages: list[Any]) -> str:
    """The user line after the latest assistant line, or an empty string.

    A user line that is already behind an assistant reply is an older
    turn. The listener must not treat that older line as this stage.
    """
    last_assistant = -1
    last_user = -1
    last_user_text = ""
    for index, message in enumerate(messages):
        role = _role_of(message)
        text = _text_of(message).strip()
        if not text:
            continue
        if role in {"assistant", "agent"}:
            last_assistant = index
        elif role == "user":
            last_user = index
            last_user_text = text
    if last_user > last_assistant:
        return last_user_text
    return ""


def _is_bare_yes(text: str) -> bool:
    """True when the whole reply is one yes token, such as ``Yes.``"""
    folded = _norm_match(text)
    if not folded or not _is_yes(text):
        return False
    return folded in {_norm_match(token) for token in _YES}


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
    the staged sentence. The transcript that asked for the save, and
    any final that arrives before that sentence is spoken, do not save
    and do not drop the stage. Any other reply after the sentence drops
    it. One reply writes once.
    """

    def __init__(self, mem: FAFMemory, session: Any | None = None) -> None:
        self._mem = mem
        self._session = session
        self._attached = False
        self._heard_assistant = ""
        self._open_text = ""
        self._open_item: str | None = None
        self._request_text = ""
        self._request_item: str | None = None
        self._has_snapshot = False
        self._assistant_at_stage = ""
        self._claimed = False
        self._finished = False
        self._last_user_final = ""
        self._nudged_for = ""

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

    def note_stage(self) -> None:
        """Snapshot the turn that caused this stage.

        The realtime model can speak, and the socket can deliver the
        request's final transcript, before that request is the latest
        user line. That transcript is not the holder's answer.
        """
        self._finished = False
        self._claimed = False
        self._has_snapshot = True
        self._assistant_at_stage = self._assistant_text(None)
        if not self._request_text and self._open_text:
            self._request_text = self._open_text
            self._request_item = self._open_item
        user = _user_after_last_assistant(_messages_from(self._session))
        if user and not _is_bare_yes(user):
            if len(_norm_match(user)) >= len(_norm_match(self._request_text)):
                self._request_text = user

    def _on_item(self, ev: Any) -> None:
        item = getattr(ev, "item", None)
        if item is None:
            return
        if _role_of(item) not in {"assistant", "agent"}:
            return
        text = _text_of(item)
        if text.strip():
            self._heard_assistant = text
            self._maybe_nudge_etch()

    def _remember_request(self) -> str:
        """The holder line that asked to remember, if that is the latest one."""
        heard = _last_user_text(_messages_from(self._session))
        if _asks_to_remember(heard):
            return heard
        if _asks_to_remember(self._last_user_final):
            return self._last_user_final
        if _asks_to_remember(self._open_text):
            return self._open_text
        return ""

    def _maybe_nudge_etch(self) -> None:
        """Ask once when the model stopped without staging a remember request.

        grok-voice-3.0 often ends the turn on the bridge ("Got it.") and
        never calls ``etch_memory``. One ``generate_reply`` asks it to
        call the tool. The model still stages. This does not write.
        """
        if self._mem.pending is not None or self._mem.merge_armed:
            return
        request = self._remember_request()
        folded = _norm_match(request)
        if not folded or folded == self._nudged_for:
            return
        generate = getattr(self._session, "generate_reply", None)
        if not callable(generate):
            return
        self._nudged_for = folded
        try:
            generate(instructions=_ETCH_NUDGE)
        except Exception as exc:
            log.warning("holder confirm: etch nudge failed: %s", exc)

    def _on_user_input(self, ev: Any) -> None:
        """Final transcripts only. Interim replies do not save or drop."""
        transcript = str(getattr(ev, "transcript", "") or "")
        item_id = getattr(ev, "item_id", None)
        if getattr(ev, "is_final", False) and transcript.strip():
            self._last_user_final = transcript
        if not getattr(ev, "is_final", False):
            if transcript.strip():
                self._open_text = transcript
                self._open_item = item_id
                if not self._sentence_out(None):
                    self._request_text = transcript
                    self._request_item = item_id
            return
        if self._finished or self._claimed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.apply_final(transcript, item_id=item_id))
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

    def _sentence_out(self, messages: list[Any] | None) -> bool:
        """True once the confirmation sentence ends the last assistant line."""
        assistant = self._assistant_text(messages)
        pending = self._mem.pending
        if pending is not None and _ends_with_spoken(assistant, str(pending["spoken"])):
            return True
        return _ends_with_spoken(assistant, SAVE_SPOKEN)

    def _is_request_echo(self, transcript: str, item_id: str | None) -> bool:
        """True when this final is the request, delivered after the stage.

        xAI flushes that utterance as ``is_final`` when the model starts
        speaking. A bare ``Yes.`` is not an echo of an older yes: the
        holder's answer often uses the same word, on a new item.
        """
        if item_id and self._request_item and item_id == self._request_item:
            return True
        req = _norm_match(self._request_text)
        norm = _norm_match(transcript)
        if not req or not norm or _is_bare_yes(transcript):
            return False
        same = norm == req or req.startswith(norm + " ")
        if not same:
            return False
        if item_id and self._request_item and item_id != self._request_item:
            return False
        return True

    def _ignore_final(
        self,
        transcript: str,
        item_id: str | None,
        messages: list[Any] | None,
    ) -> bool:
        """The request, or a final that arrives before the sentence."""
        if self._is_request_echo(transcript, item_id):
            log.info("holder confirm: ignored request transcript")
            return True
        if not self._has_snapshot:
            return False
        assistant = self._assistant_text(messages)
        if _norm_match(assistant) != _norm_match(self._assistant_at_stage):
            return False
        # Same assistant line as at stage time. If that line already ends
        # with the confirmation sentence, this final is the answer.
        if self._sentence_out(messages):
            return False
        log.info("holder confirm: ignored transcript before the sentence")
        return True

    async def apply_final(
        self,
        transcript: str,
        *,
        messages: list[Any] | None = None,
        item_id: str | None = None,
    ) -> str:
        """Decide the staged fact, or a scratchpad save, from one final line.

        ``transcript`` is the final user-input-transcribed text. It is
        not read from session history. The last assistant message is.
        A duplicate final of the same reply does not write again.
        """
        if self._finished or self._claimed:
            return ""
        if self._ignore_final(transcript, item_id, messages):
            return ""
        self._claimed = True
        try:
            if self._mem.pending is not None:
                return await self._resolve_fact(transcript, messages)
            return await self._resolve_merge(transcript, messages)
        finally:
            if not self._finished:
                self._claimed = False

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
            self._finished = True
            return await self._commit(pending)
        self._finished = True
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
        if self._mem.saved_merge_result():
            self._finished = True
            return ""
        assistant = self._assistant_text(messages)
        armed = self._mem.merge_armed
        matches = _is_yes(transcript) and _ends_with_spoken(assistant, SAVE_SPOKEN)
        if matches:
            self._finished = True
            self._mem.clear_merge_arm()
            return await self._run_merge()
        if armed:
            self._finished = True
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
