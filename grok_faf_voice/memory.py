"""FAFMemory — live voice memory layer for Grok Voice agents.

The Voice Memory Layer that the static `.faf` (FAFContext) can't
provide: audio-native, session-persistent primitives. At Gate 2:
etch (write_soul) + recall (get_soul) over MCPaaS, plus a Scratchpad
for in-session ephemeral state.

Persistence is via the MCP protocol at `https://mcpaas.live/mcp`,
served over Streamable HTTP. Auth is per-soul via an MCPaaS token
(env `MCPAAS_TOKEN` or constructor arg).

Future gates extend the surface:
- Gate 3: Paralinguistic Tags (.fafm schema extension)
- Gate 4: Smart Merge Engine (scratchpad → soul at session end)
- Gate 5: Voice Session Ledger (cross-call welcome-back)
- Gate 6: Real-time Context Bus (sub-80ms mid-stream context mutation)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp import Client
from fastmcp.exceptions import ToolError

from grok_faf_voice.scratchpad import Scratchpad

MCPAAS_URL = "https://mcpaas.live/mcp"

# Suppress benign fastmcp shutdown noise. The Streamable HTTP
# transport emits "Session termination failed: 404" on every clean
# close against mcpaas.live — protocol works, only graceful-shutdown
# emits the warning. See `reference-mcpaas-write-soul-tool.md` notes.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)


class FAFAuthRequiredError(RuntimeError):
    """Raised when an op requires an MCPaaS token but none is configured.

    The friendly message guides the dev to the Voice key flow without
    raising a low-level ToolError or surfacing implementation details.
    """


class FAFEtchError(RuntimeError):
    """Raised when an etch operation fails server-side.

    Wraps lower-level fastmcp/MCP errors with friendly text that names
    the likely cause (invalid soul, allowlist violation, etc.). The
    original exception is chained via `__cause__` for debugging.
    """


class FAFRecallError(RuntimeError):
    """Raised when a recall (get_soul) operation fails server-side.

    Wraps lower-level fastmcp/MCP errors with friendly text. Original
    exception chained via `__cause__`.
    """


class FAFMemory:
    """Live voice memory for a Grok Voice agent.

    Wraps the MCPaaS `write_soul` / `get_soul` MCP tools and exposes
    them as clean async methods. Composes a `Scratchpad` for in-session
    ephemeral state.

    Parameters
    ----------
    soul : str
        The MCPaaS soul name to read/write. Must be in the MCPaaS
        allowlist; an invalid soul raises `fastmcp.exceptions.ToolError`
        on the first write. Read calls (`get`) return the soul's
        current state if it exists.
    token : str, optional
        MCPaaS auth token. Falls back to env var `MCPAAS_TOKEN` if not
        provided. Required for writes; reads work without a token for
        public souls.
    mcp_url : str, optional
        Override the MCP endpoint URL. Defaults to `mcpaas.live/mcp`.

    Examples
    --------
    >>> mem = FAFMemory("grok", token="wolfe-68-orange")
    >>> await mem.etch("Gate 2 shipped", type="note", tags=["sdk", "milestone"])
    >>> soul_text = await mem.get()

    LiveKit integration::

        session = AgentSession(
            llm=xai.realtime.RealtimeModel(voice="Ara"),
            instructions=ctx.system_prompt(),
            tools=mem.tools(),  # adds etch_memory + recall_memory
        )
    """

    def __init__(
        self,
        soul: str,
        *,
        token: str | None = None,
        mcp_url: str = MCPAAS_URL,
    ) -> None:
        self._soul = soul
        self._token = token or os.environ.get("MCPAAS_TOKEN")
        self._mcp_url = mcp_url
        self._scratchpad = Scratchpad()

    @property
    def soul(self) -> str:
        """The soul name this FAFMemory is bound to."""
        return self._soul

    @property
    def scratchpad(self) -> Scratchpad:
        """The in-session scratchpad. Ephemeral; cleared on init."""
        return self._scratchpad

    async def etch(
        self,
        entry: str,
        *,
        type: str = "note",
        tags: list[str] | None = None,
    ) -> str:
        """Write an entry to the eternal soul via MCPaaS write_soul.

        Returns the server's confirmation message text.

        Raises
        ------
        FAFAuthRequiredError
            No token is set (constructor arg or `MCPAAS_TOKEN` env). Etch
            requires a Voice key. Friendly upgrade prompt included.
        fastmcp.exceptions.ToolError
            For server-side errors (invalid soul, etc.).
        """
        if not self._token:
            raise FAFAuthRequiredError(
                "To save memories, get your free Voice key — coming soon. "
                "For now, FAFMemory.get() works read-only against public souls."
            )

        args: dict[str, Any] = {
            "soul": self._soul,
            "entry": entry,
            "type": type,
            "token": self._token,
        }
        if tags:
            args["tags"] = tags

        try:
            async with Client(self._mcp_url) as client:
                result = await client.call_tool("write_soul", args)
                return _first_text(result)
        except ToolError as e:
            raise self._wrap_tool_error(e, op="etch") from e

    async def get(self) -> str:
        """Read the soul's current state via MCPaaS get_soul.

        Returns the soul body as a string. Includes the server's
        preamble before the first `\\n---\\n` separator; downstream
        consumers can split if they want only the YAML body.
        """
        try:
            async with Client(self._mcp_url) as client:
                result = await client.call_tool("get_soul", {"soul": self._soul})
                return _first_text(result)
        except ToolError as e:
            raise self._wrap_tool_error(e, op="recall") from e

    def _wrap_tool_error(
        self, exc: ToolError, *, op: str
    ) -> FAFEtchError | FAFRecallError:
        """Build a friendly error from a low-level ToolError.

        Recognises common server-side failures and prefixes them with
        actionable guidance. Falls back to a generic wrapper otherwise.
        """
        msg = str(exc)
        cls: type = FAFEtchError if op == "etch" else FAFRecallError

        if "Invalid soul" in msg:
            return cls(
                f"Soul '{self._soul}' isn't on MCPaaS. {msg.strip()}"
            )

        return cls(f"{op.capitalize()} failed: {msg.strip()}")

    # ----------------------------------------------------------------
    # Paralinguistic markers (Gate 3)
    #
    # Voice memory captures HOW the user spoke, not just WHAT was said.
    # Markers are stored as standard `write_soul` entries with a
    # consistent inline prefix and the `paralinguistic` tag, so they
    # can be retrieved + summarised on subsequent sessions.
    #
    # Canonical marker types (free-form strings accepted):
    #   - "tone" — e.g. frustrated, excited, calm, urgent
    #   - "emotional_state" — e.g. stressed, happy, focused
    #   - "speaking_style" — e.g. fast, slow, deliberate, hesitant
    #   - "interruption_pattern" — e.g. frequent, rare, after silence
    # ----------------------------------------------------------------

    PARALINGUISTIC_PREFIX = "[paralinguistic]"
    PARALINGUISTIC_TAG = "paralinguistic"

    async def etch_paralinguistic(
        self,
        marker_type: str,
        value: str,
        *,
        context: str | None = None,
    ) -> str:
        """Record a paralinguistic marker (HOW the user spoke).

        Stored as a standard write_soul entry, prefixed and tagged for
        downstream retrieval by `paralinguistic_summary`.

        Parameters
        ----------
        marker_type : str
            Canonical types: 'tone', 'emotional_state', 'speaking_style',
            'interruption_pattern'. Other strings accepted but may not
            be recognised by future enrichment passes.
        value : str
            The marker value, e.g. 'frustrated', 'fast-paced'.
        context : str, optional
            What was being discussed when the marker was observed.

        Examples
        --------
        >>> await mem.etch_paralinguistic(
        ...     "tone", "frustrated", context="checkout flow"
        ... )
        """
        entry_parts = [
            self.PARALINGUISTIC_PREFIX,
            f"{marker_type}: {value}",
        ]
        if context:
            entry_parts.append(f"— {context}")
        entry = " ".join(entry_parts)

        return await self.etch(
            entry,
            type="paralinguistic",
            tags=[self.PARALINGUISTIC_TAG, marker_type],
        )

    async def paralinguistic_summary(self, *, max_recent: int = 5) -> str:
        """Build a one-line synopsis of recent paralinguistic markers.

        Suitable for injection into the next session's system prompt
        so the agent opens with awareness of HOW the user has been
        sounding across prior sessions.

        Returns an empty string if the soul has no paralinguistic
        entries yet — caller can compose accordingly.

        Parameters
        ----------
        max_recent : int, default 5
            How many recent paralinguistic entries to surface.
        """
        soul_text = await self.get()
        markers = _extract_paralinguistic_lines(
            soul_text, prefix=self.PARALINGUISTIC_PREFIX
        )

        if not markers:
            return ""

        recent = markers[-max_recent:]
        return "Prior voice context: " + "; ".join(recent)

    def tools(self) -> list:
        """Return LiveKit `@function_tool` wrappers for the agent.

        At Gate 3 the trio: etch_memory, recall_memory, note_paralinguistic.
        Pass into the Agent (or AgentSession) `tools=` list to expose
        memory commands to the voice agent.
        """
        from grok_faf_voice.tools import (
            make_etch_tool,
            make_paralinguistic_tool,
            make_recall_tool,
        )

        return [
            make_etch_tool(self),
            make_recall_tool(self),
            make_paralinguistic_tool(self),
        ]


def _first_text(result: Any) -> str:
    """Extract the first text payload from a fastmcp CallToolResult.

    fastmcp returns `result.content` as `list[TextContent]`. We surface
    the first item's `.text`; if the structure is unexpected, fall back
    to `str(content)`.
    """
    content = getattr(result, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text
    return str(content)


def _extract_paralinguistic_lines(soul_text: str, *, prefix: str) -> list[str]:
    """Pull paralinguistic-prefixed lines out of a soul body.

    Soul entries render as `• <text> [tag1, tag2]` in MCPaaS output.
    We grep for the prefix marker and strip leading bullet/whitespace,
    returning the text portion (without the trailing tags) for clean
    summary composition.
    """
    out: list[str] = []
    for raw in soul_text.splitlines():
        if prefix not in raw:
            continue
        line = raw.lstrip("•· ").strip()
        # Drop trailing tag block "[tag1, tag2]" so the summary
        # reads cleanly without bracket noise.
        if " [" in line:
            line = line.rsplit(" [", 1)[0].rstrip()
        # Strip the prefix itself for tighter summary text.
        if line.startswith(prefix):
            line = line[len(prefix):].lstrip()
        if line:
            out.append(line)
    return out
