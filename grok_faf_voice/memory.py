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

import os
from typing import Any

from fastmcp import Client

from grok_faf_voice.scratchpad import Scratchpad

MCPAAS_URL = "https://mcpaas.live/mcp"


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
        Raises `fastmcp.exceptions.ToolError` on invalid soul or auth.
        """
        args: dict[str, Any] = {
            "soul": self._soul,
            "entry": entry,
            "type": type,
        }
        if tags:
            args["tags"] = tags
        if self._token:
            args["token"] = self._token

        async with Client(self._mcp_url) as client:
            result = await client.call_tool("write_soul", args)
            return _first_text(result)

    async def get(self) -> str:
        """Read the soul's current state via MCPaaS get_soul.

        Returns the soul body as a string. Includes the server's
        preamble before the first `\\n---\\n` separator; downstream
        consumers can split if they want only the YAML body.
        """
        async with Client(self._mcp_url) as client:
            result = await client.call_tool("get_soul", {"soul": self._soul})
            return _first_text(result)

    def tools(self) -> list:
        """Return LiveKit `@function_tool` wrappers (etch + recall).

        Pass into `AgentSession(tools=mem.tools())` to expose memory
        commands to the voice agent.
        """
        from grok_faf_voice.tools import make_etch_tool, make_recall_tool

        return [make_etch_tool(self), make_recall_tool(self)]


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
