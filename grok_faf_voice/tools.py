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

Use via `FAFMemory.tools(session)`:

    mem = FAFMemory("grok", token=...)
    session = AgentSession(llm=xai.realtime.RealtimeModel(voice="Ara"))
    agent = Agent(instructions=..., tools=mem.tools(session))
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livekit.agents import RunContext, function_tool

if TYPE_CHECKING:
    from livekit.agents import AgentSession

    from grok_faf_voice.memory import FAFMemory


def make_etch_tool(mem: FAFMemory):
    """Return an @function_tool that etches voice content to FAFMemory."""
    from grok_faf_voice.memory import FAFAuthRequiredError, FAFEtchError

    @function_tool
    async def etch_memory(context: RunContext, content: str) -> str:
        """Save content to persistent eternal memory.

        Use when the user says "etch this", "remember this",
        "save that", or otherwise asks to record something durably.
        The content will survive across sessions, devices, and
        model switches.

        CRITICAL LATENCY RULE (must follow):
        - ALWAYS speak a short natural bridge FIRST (under 400ms):
          "Got it.", "Noted.", "I'll remember that.", "Jotting that
          down.", or "Understood." — THEN call this tool.
        - Never go silent. The realtime stream emits no filler audio
          during tool execution.
        """
        try:
            await mem.etch(content, type="memory")
            return f"Etched: {content}"
        except FAFAuthRequiredError as e:
            return str(e)
        except FAFEtchError as e:
            return f"Could not save: {e}"

    return etch_memory


def make_recall_tool(mem: FAFMemory):
    """Return an @function_tool that reads the current soul state."""
    from grok_faf_voice.memory import FAFRecallError

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
        try:
            return await mem.get()
        except FAFRecallError as e:
            return f"Could not recall: {e}"

    return recall_memory


def make_paralinguistic_tool(mem: FAFMemory):
    """Return an @function_tool that records paralinguistic markers.

    The model decides when the user's tone/style/emotional state is
    worth recording — typically when:
      - The user sounds notably frustrated, excited, calm, urgent
      - The user's speaking pace shifts (faster when stressed, etc.)
      - The user interrupts frequently or pauses notably
    """
    from grok_faf_voice.memory import FAFAuthRequiredError, FAFEtchError

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
        try:
            await mem.etch_paralinguistic(
                marker_type, value, context=topic
            )
            return f"Noted: {marker_type}={value}"
        except FAFAuthRequiredError as e:
            return str(e)
        except FAFEtchError as e:
            return f"Could not note: {e}"

    return note_paralinguistic


def make_merge_tool(mem: FAFMemory, session: AgentSession):
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
        try:
            await session.say(
                "Give me just a moment while I consolidate everything..."
            )
            result = await mem.merge(strategy="grok-decides")
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

    return merge_now
