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

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from grok_faf_voice.scratchpad import Scratchpad, ScratchpadEntry

if TYPE_CHECKING:
    from livekit.agents import AgentSession

MCPAAS_URL = "https://mcpaas.live/mcp"
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
XAI_CHAT_MODEL_DEFAULT = "grok-4-1-fast-non-reasoning"

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


class FAFMergeError(RuntimeError):
    """Raised when a Smart Merge operation fails (LLM call, decision
    parsing, or any sub-etch). Original exception chained via __cause__.
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

    # ----------------------------------------------------------------
    # Smart Merge Engine (Gate 4)
    #
    # Promotes Scratchpad entries to permanent soul memory at session
    # end. Two strategies: a fast/free heuristic (default), and a
    # Grok-decides LLM call (opt-in).
    # ----------------------------------------------------------------

    MERGE_TAG = "merged"
    EPHEMERAL_PRIORITY = "ephemeral"

    async def merge(
        self,
        *,
        strategy: str = "heuristic",
        chat_model: str = XAI_CHAT_MODEL_DEFAULT,
    ) -> dict[str, Any]:
        """Promote scratchpad entries to permanent soul memory.

        Strategies:

        - ``"heuristic"`` (default): keep everything except entries
          with ``priority="ephemeral"``. Free, fast, deterministic.

        - ``"grok-decides"``: send the scratchpad to Grok with a
          meta-prompt asking which entries are worth permanent
          memory. Smarter, but costs tokens. Returns conservatively
          on parse failure (keeps everything).

        - ``"merge_all"``: promote every entry regardless of metadata.

        Clears the scratchpad after a successful merge.

        Returns
        -------
        dict
            ``{"promoted": int, "discarded": int, "strategy": str}``
        """
        entries = self._scratchpad.all_entries()
        if not entries:
            return {"promoted": 0, "discarded": 0, "strategy": strategy}

        if strategy == "heuristic":
            promoted, discarded = self._heuristic_split(entries)
        elif strategy == "grok-decides":
            try:
                promoted, discarded = await self._grok_decides_split(
                    entries, chat_model=chat_model
                )
            except Exception as e:
                raise FAFMergeError(
                    f"grok-decides strategy failed: {e}. Try strategy='heuristic'."
                ) from e
        elif strategy == "merge_all":
            promoted, discarded = list(entries.items()), []
        else:
            raise ValueError(
                f"Unknown merge strategy: {strategy!r}. "
                f"Valid: 'heuristic', 'grok-decides', 'merge_all'."
            )

        # Etch each promoted entry with [merged] tag for identification
        for key, entry in promoted:
            tags = [self.MERGE_TAG]
            if entry.tag:
                tags.append(entry.tag)
            await self.etch(
                f"{key}: {entry.value}",
                type="memory",
                tags=tags,
            )

        # Clear scratchpad after successful merge
        self._scratchpad.clear()

        return {
            "promoted": len(promoted),
            "discarded": len(discarded),
            "strategy": strategy,
        }

    def _heuristic_split(
        self, entries: dict[str, ScratchpadEntry]
    ) -> tuple[list, list]:
        """Default split: discard ephemeral, keep everything else."""
        promoted = []
        discarded = []
        for key, entry in entries.items():
            if entry.priority == self.EPHEMERAL_PRIORITY:
                discarded.append((key, entry))
            else:
                promoted.append((key, entry))
        return promoted, discarded

    async def _grok_decides_split(
        self,
        entries: dict[str, ScratchpadEntry],
        *,
        chat_model: str,
    ) -> tuple[list, list]:
        """Ask Grok which entries deserve permanent memory.

        Hits xAI's chat completions REST endpoint directly (same
        pattern as utils/transcribe.py). Returns conservatively
        (keep everything) on any parse failure.
        """
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "XAI_API_KEY not set — required for strategy='grok-decides'."
            )

        items_json = json.dumps(
            {
                k: {"value": e.value, "priority": e.priority, "tag": e.tag}
                for k, e in entries.items()
            },
            indent=2,
        )
        prompt = (
            "You are a memory consolidator for a voice AI agent. "
            "Below is a scratchpad of in-session items. Decide which "
            "are worth saving as permanent memories vs which are "
            "ephemeral. Permanent items are facts/preferences/decisions "
            "that future sessions should know. Ephemeral items are "
            "transient (URLs mentioned once, throwaway notes, etc.). "
            "Respond with JSON only, no prose:\n"
            '  {"keep": ["key1", "key2"], "discard": ["key3"]}\n\n'
            f"Scratchpad:\n{items_json}"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                XAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": chat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                },
            )
            resp.raise_for_status()
            payload = resp.json()

        # Parse decision; fall back conservative on any issue
        try:
            content = payload["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if Grok wrapped the JSON
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            decision = json.loads(content)
            keep = set(decision.get("keep", []))
            discard = set(decision.get("discard", []))
        except (KeyError, json.JSONDecodeError, IndexError):
            # Conservative fallback: keep everything
            return list(entries.items()), []

        promoted = []
        discarded = []
        for key, entry in entries.items():
            if key in discard and key not in keep:
                discarded.append((key, entry))
            else:
                promoted.append((key, entry))
        return promoted, discarded

    def attach_auto_merge(
        self,
        session: AgentSession,
        *,
        strategy: str = "heuristic",
    ) -> None:
        """Hook ``session.on("close")`` so merge fires automatically.

        Convenience for devs who want session-end persistence without
        wiring it explicitly. Library callers can still call
        ``await mem.merge()`` manually — both work.

        Failures during auto-merge are logged but never raised, so a
        merge crash can't take down the session lifecycle.
        """

        @session.on("close")
        def _on_close(*_args: Any, **_kwargs: Any) -> None:
            async def _do() -> None:
                try:
                    await self.merge(strategy=strategy)
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "auto-merge failed (non-fatal): %s", e
                    )

            try:
                asyncio.create_task(_do())
            except RuntimeError:
                # No running loop (rare during shutdown). Best-effort.
                pass

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

        At Gate 4: etch_memory + recall_memory + note_paralinguistic
        + merge_now. Pass into the Agent (or AgentSession) ``tools=``
        list to expose memory commands to the voice agent.
        """
        from grok_faf_voice.tools import (
            make_etch_tool,
            make_merge_tool,
            make_paralinguistic_tool,
            make_recall_tool,
        )

        return [
            make_etch_tool(self),
            make_recall_tool(self),
            make_paralinguistic_tool(self),
            make_merge_tool(self),
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
