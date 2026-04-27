"""LiveKit @function_tool wrappers for FAFMemory.

The voice agent triggers these naturally when the user says something
like "etch this..." or "what do you remember?". The decorator surfaces
them as callable tools to the realtime model.

Use via `FAFMemory.tools()`:

    mem = FAFMemory("grok", token=...)
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(voice="Ara"),
        tools=mem.tools(),
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livekit.agents import RunContext, function_tool

if TYPE_CHECKING:
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
        """
        try:
            return await mem.get()
        except FAFRecallError as e:
            return f"Could not recall: {e}"

    return recall_memory
