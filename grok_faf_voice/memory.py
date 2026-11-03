"""FAFMemory — live voice memory for Grok Voice agents.

The audio-native, session-persistent primitives the static `.faf`
(FAFContext) can't provide: etch (write_soul) + recall (get_soul)
over MCPaaS, plus a Scratchpad for in-session ephemeral state, a
Smart Merge Engine that promotes scratchpad → soul at session end,
a Voice Session Ledger for audit + cross-session resumption, and
paralinguistic markers (how the user spoke, not just what they said).

Persistence is via the MCP protocol at `https://mcpaas.live/mcp`,
served over Streamable HTTP. Auth is per-soul via an MCPaaS API
key (env `MCPAAS_API_KEY` or constructor arg `api_key=`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import yaml
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from grok_faf_voice._merge_models import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    MergePayloadEntry,
    MergeResult,
)
from grok_faf_voice.context_bus import BusEvent, ContextBus
from grok_faf_voice.forget import (
    FAFForgetError,
    ForgetCriteria,
    apply_forget,
    split_hosted_banner,
)
from grok_faf_voice.ledger import NullVoiceSessionLedger, VoiceSessionLedger
from grok_faf_voice.scratchpad import Scratchpad, ScratchpadEntry

if TYPE_CHECKING:
    from livekit.agents import AgentSession, JobContext

MCPAAS_URL = "https://mcpaas.live/mcp"
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"

# 2026-05-11: mcpaas-cf upstream flipped to strict-default Sessionless MCP per
# SEP-2567 + SEP-2575. fastmcp.Client(URL) doesn't send the now-required spec
# headers (Mcp-Method, MCP-Protocol-Version). To preserve back-compat, we
# construct an explicit StreamableHttpTransport carrying X-MCP-Mode: flexi —
# opts into mcpaas-cf's co-existence mode (validate headers when present,
# accept when absent). Drop when fastmcp upstream ships full spec-header support.
# Audit: PLANET-FAF/IMCP/audit-mcpaas-mcp-state-2026-05-11.md
# Doctrine: memory/mcpaas-coexistence-strict-default-x-mcp-mode.md
_MCPAAS_MODE_HEADER = {"X-MCP-Mode": "flexi"}
# Reasoning variant — same price as non-reasoning, judgment-grade for
# memory consolidation. The reasoning trace dramatically improves
# recency vs importance, emotional weight, and over-consolidation calls.
XAI_CHAT_MODEL_DEFAULT = "grok-4-1-fast-reasoning"

# System-prompt reinforcement for tool latency discipline. Append to your
# Agent's instructions so the model speaks a short bridge phrase before
# calling any FAFMemory tool — the realtime stream goes silent during
# tool execution otherwise.
LATENCY_BRIDGE_INSTRUCTIONS = """
CRITICAL LATENCY RULE (follow exactly):
- For etch_memory / recall_memory / note_paralinguistic:
  speak a short natural bridge FIRST ("Got it.", "Let me check..."), then
  invoke the tool. Never go silent during tool execution. Never speak
  the tool name, the arguments, or any tool-call markup.
- For merge_now: invoke the tool, then say only the sentence it returns,
  and stop. Do not speak the tool call. The save can take several
  seconds after the holder agrees.

CONFIRM BEFORE WRITE (follow exactly):
- You do not know the confirmation sentence until a tool returns it.
  Do not invent a sentence. Do not say that anything was saved.
- Invoke etch_memory with the fact in that same turn. "Got it." by
  itself is not a finished reply. The tool stages the fact. It does
  not save it. Then say only the sentence it returns, and stop. Do not
  invoke another tool to save it. Do not speak the tool call.
- The SDK saves when the namepoint holder's next final reply is a yes.
- A second voice in the room is not a confirmation.
- note_paralinguistic follows the same rule: invoke it, say only the
  sentence it returns, then stop. Do not speak the tool call.
- merge_now stages a save. Invoke it, say only the sentence it returns,
  then stop. The SDK saves when the holder agrees.

ETCH DISCIPLINE (follow exactly):
- etch_memory must receive the ACTUAL fact or detail, never the user's
  intent to remember. If the user says "I want to make a memory" or
  "remember this" without saying WHAT, ask "What would you like me
  to remember?" and WAIT for the substance. THEN stage only the substance.
- Never stage meta-statements like "user wants to make a memory".
- One fact = one stage. Don't fire etch_memory before the user has
  finished giving you the content.
""".strip()

# The sentence the agent must speak before a staged fact can be saved.
SPOKEN_FACT = "I'll remember this: {content}."

# Suppress benign fastmcp shutdown noise. The Streamable HTTP
# transport emits "Session termination failed: 404" on every clean
# close against mcpaas.live — protocol works, only graceful-shutdown
# emits the warning. See `reference-mcpaas-write-soul-tool.md` notes.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)


class FAFAuthRequiredError(RuntimeError):
    """Raised when an op requires an MCPaaS API key but none is configured.

    The friendly message guides the dev to the Voice API key flow
    without raising a low-level ToolError or surfacing implementation
    details.
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
    api_key : str, optional
        MCPaaS API key. Falls back to env var `MCPAAS_API_KEY` if not
        provided. Required for writes; reads work without an API key
        for public souls.
    mcp_url : str, optional
        Override the MCP endpoint URL. Defaults to `mcpaas.live/mcp`.

    Examples
    --------
    >>> mem = FAFMemory("grok", api_key=os.environ["MCPAAS_API_KEY"])
    >>> await mem.etch("first memory", type="note", tags=["sdk", "milestone"])
    >>> soul_text = await mem.get()

    LiveKit integration::

        session = AgentSession(
            llm=xai.realtime.RealtimeModel(
                voice="Ara",
                turn_detection=xai.realtime.TurnDetection(
                    type="server_vad",
                    threshold=0.85,
                    silence_duration_ms=500,
                    prefix_padding_ms=333,
                    create_response=True,
                ),
            ),
        )
        agent = Agent(instructions=ctx.system_prompt(), tools=mem.tools(session))
        mem.attach_auto_merge(session, ctx, strategy="grok-decides")
    """

    def __init__(
        self,
        soul: str,
        *,
        api_key: str | None = None,
        mcp_url: str = MCPAAS_URL,
        ledger: VoiceSessionLedger | None = None,
        bus: ContextBus | None = None,
        local_path: str | Path | None = None,
        shared_device: bool = False,
        speaker_is_holder: bool = True,
        child_may_be_speaking: bool = False,
    ) -> None:
        self._soul = soul
        self._api_key = api_key or os.environ.get("MCPAAS_API_KEY")
        self._mcp_url = mcp_url
        # Local mode: when set (via from_file), reads/writes a .fafm on disk
        # instead of MCPaaS — no API key required. None => MCPaaS (default).
        self._local_path = Path(local_path) if local_path is not None else None
        # Cached soul text from the last get() (or a direct local read), so the
        # parsed accessors (.facts / .profile / .index) don't re-fetch.
        self._cache: str | None = None
        self._scratchpad = Scratchpad()
        self.ledger: VoiceSessionLedger = ledger or NullVoiceSessionLedger()
        # Context Bus — async pub/sub for voice-memory events. Created
        # eagerly so subscribers can register before any session work
        # begins. Auto-starts on first emit; explicit start_bus() is
        # available for callers that prefer determinism.
        self._bus: ContextBus = bus or ContextBus()
        # Guards for compose-with-merge_now. Set to True at the success
        # tail of `merge()` so the shutdown callback can no-op when the
        # explicit user-triggered merge already ran.
        self._merge_completed_this_session = False
        self._merge_in_progress = False
        self._lock = asyncio.Lock()
        # Ambient privacy. No voice-id gate: a shared device, a speaker
        # who is not the holder, or a child in the room means this
        # session does not etch. There is no namepoint for a child.
        self._shared_device = shared_device
        self._speaker_is_holder = speaker_is_holder
        self._child_may_be_speaking = child_may_be_speaking
        self._pending: dict[str, Any] | None = None
        self._session_only_n = 0
        self._merge_armed = False
        self._last_confirm_result = ""
        self._last_merge_result = ""
        self._confirm: Any | None = None

    @property
    def soul(self) -> str:
        """The soul name this FAFMemory is bound to."""
        return self._soul

    @property
    def scratchpad(self) -> Scratchpad:
        """The in-session scratchpad. Ephemeral; cleared on init."""
        return self._scratchpad

    @property
    def bus(self) -> ContextBus:
        """The Context Bus — async pub/sub for voice-memory events.

        Subscribers register via ``mem.bus.on(BusEvent.X, handler)`` or
        ``mem.bus.on_sync(BusEvent.X, handler)``. The bus auto-starts on
        first emit; call ``await mem.start_bus()`` to start it eagerly.
        """
        return self._bus

    async def start_bus(self) -> None:
        """Start the bus's dispatcher task eagerly.

        Optional — the bus auto-starts on the first ``emit`` call.
        Useful when you want subscribers to be active before any
        session work begins, e.g. in agent entrypoints.
        """
        await self._bus.start()

    async def stop_bus(self) -> None:
        """Stop the bus's dispatcher task. Idempotent."""
        await self._bus.stop()

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
            No API key is set (constructor arg or `MCPAAS_API_KEY` env).
            Etch requires a Voice API key. Friendly upgrade prompt
            included.
        fastmcp.exceptions.ToolError
            For server-side errors (invalid soul, etc.).
        """
        if not self._api_key:
            raise FAFAuthRequiredError(
                "To save memories, get your free Voice API key — "
                "coming soon. For now, FAFMemory.get() works read-only "
                "against public souls."
            )

        # Wire field name `token` is the MCPaaS server-side contract;
        # the SDK exposes it as `api_key` to the user (matches XAI_API_KEY
        # / LIVEKIT_API_KEY ecosystem). The wire field will rename to
        # `api_key` once MCPaaS server-side updates — until then, the
        # SDK passes self._api_key through the legacy `token` field.
        args: dict[str, Any] = {
            "soul": self._soul,
            "entry": entry,
            "type": type,
            "token": self._api_key,
        }
        if tags:
            args["tags"] = tags

        # Sessionless MCP compat (2026-05-11): explicit transport with flexi header.
        transport = StreamableHttpTransport(url=self._mcp_url, headers=_MCPAAS_MODE_HEADER)
        try:
            async with Client(transport) as client:
                result = await client.call_tool("write_soul", args)
                text = _first_text(result)
        except ToolError as e:
            raise self._wrap_tool_error(e, op="etch") from e

        await self._bus.emit(
            BusEvent.SOUL_UPDATED,
            {"soul": self._soul, "type": type, "tags": tags or []},
        )
        return text

    def retention(self) -> str:
        """``session-only`` or ``durable``.

        Session-only when the device is shared, the speaker is not the
        namepoint holder, or a child may be talking. No voice print is
        consulted. Any user turn confirms. The second-voice rule is
        the prompt plus the operator flags. The default is durable:
        the holder, on their device.
        """
        if (
            self._shared_device
            or not self._speaker_is_holder
            or self._child_may_be_speaking
        ):
            return "session-only"
        return "durable"

    @property
    def pending(self) -> dict[str, Any] | None:
        """The fact waiting for the holder to confirm, or None."""
        return self._pending

    def stage_memory(
        self,
        content: str,
        *,
        type: str = "memory",
        tags: list[str] | None = None,
    ) -> str:
        """Hold a fact. Does not write the soul.

        Returns the exact sentence the agent must speak before
        :meth:`commit_staged` will save it. One trailing period is
        taken off the fact before that sentence is built, so
        ``Main St.`` is spoken once.
        """
        spoken_content = content.strip()
        if spoken_content.endswith(".") and spoken_content[:-1].strip():
            spoken_content = spoken_content[:-1].rstrip()
        spoken = SPOKEN_FACT.format(content=spoken_content)
        self._pending = {
            "content": spoken_content,
            "spoken": spoken,
            "type": type,
            "tags": list(tags or []),
        }
        self._note_confirm_stage()
        return spoken

    async def commit_staged(self, content: str, *, holder_confirmed: bool) -> str:
        """Save a staged fact after the holder confirms the spoken sentence.

        ``holder_confirmed`` comes from the SDK listener, which reads the
        holder's final transcript. There is no model tool for it.
        Session-only retention writes the scratchpad and does not call
        :meth:`etch`.
        """
        pending = self._pending
        if pending is None or pending["content"] != content:
            raise FAFEtchError("Nothing staged matches that fact.")
        if not holder_confirmed:
            raise FAFEtchError(
                "The namepoint holder has not confirmed this fact."
            )
        if self.retention() == "session-only":
            self._session_only_n += 1
            self._scratchpad.update(
                f"session-only:{self._session_only_n}",
                content,
                priority=self.EPHEMERAL_PRIORITY,
                tag=self.SESSION_ONLY_TAG,
            )
            self._pending = None
            return "Held for this session only."
        text = await self.etch(
            content,
            type=pending["type"],
            tags=pending["tags"] or None,
        )
        self._pending = None
        return text

    def drop_pending(self) -> None:
        """Drop a staged fact. Nothing is written."""
        self._pending = None

    async def confirm_memory(self, content: str | None = None) -> str:
        """Operator and SDK only. Not a model tool.

        A call before the holder's final yes does not write and does
        not drop a staged fact. The session listener saves on that yes.
        """
        _ = content
        return self.note_confirm_result("Nothing was saved.")

    def note_confirm_result(self, text: str) -> str:
        """Remember the last confirm outcome. Returns ``text``."""
        self._last_confirm_result = text
        return text

    @property
    def last_confirm_result(self) -> str:
        """The last confirm outcome, or an empty string."""
        return self._last_confirm_result

    def _note_confirm_stage(self) -> None:
        """Tell the confirm listener that a fact or a save was just staged."""
        confirm = self._confirm
        if confirm is not None:
            confirm.note_stage()

    def arm_merge(self) -> None:
        """Remember that ``merge_now`` staged a save. The listener writes."""
        self._merge_armed = True
        self._note_confirm_stage()

    def clear_merge_arm(self) -> None:
        self._merge_armed = False

    @property
    def merge_armed(self) -> bool:
        return self._merge_armed

    def remember_merge_result(self, text: str) -> str:
        """Remember the spoken merge summary. Returns ``text``."""
        self._last_merge_result = text
        return text

    def saved_merge_result(self) -> str | None:
        """The merge summary after this session already saved, else None."""
        if self._merge_completed_this_session and self._last_merge_result:
            return self._last_merge_result
        return None

    def attach_confirm(self, session: AgentSession) -> Any:
        """Listen for the holder's next final transcript. Idempotent."""
        if self._confirm is not None and self._confirm.session is session:
            return self._confirm
        from grok_faf_voice.tools import HolderConfirm

        self._confirm = HolderConfirm(self, session)
        self._confirm.attach()
        return self._confirm

    async def forget(self, namepoint: str, criteria: ForgetCriteria) -> dict[str, int | str]:
        """Remove entries by id, tag, time range, or full wipe.

        Operator and SDK only. Not a model tool. ``delete_soul`` is not
        called. A hosted soul is rewritten with ``write_soul``
        ``replace=true`` and ``content`` set to the new body.
        ``get_soul`` returns the stored text (no banner). A leading
        banner is removed only when the remainder is the soul.
        ``write_soul`` with ``replace=true`` and ``content`` stores
        that string as the whole soul (``env.SOULS.put``, soul name
        lowercased). Read from ``mcpaas-cf`` ``handler.ts``. A live
        hosted round trip was not opened.
        A local ``.fafm`` is replaced atomically, mode 0600.
        ``removed == 0`` does not write.
        """
        if not isinstance(criteria, ForgetCriteria):
            raise FAFForgetError("criteria must be a ForgetCriteria.")
        if criteria.is_empty():
            raise FAFForgetError(
                "forget needs an entry id, a tag, a time range, or wipe=True."
            )
        if not _same_namepoint(namepoint, self._soul):
            raise FAFForgetError(
                f"namepoint {namepoint!r} does not match this soul ({self._soul!r})."
            )
        if self._local_path is not None:
            text = self._local_path.read_text(encoding="utf-8")
            new_text, removed = apply_forget(text, criteria)
            if removed:
                _write_text_owner_only(self._local_path, new_text)
                self._cache = new_text
            return {"removed": removed, "namepoint": self._soul}

        if not self._api_key:
            raise FAFAuthRequiredError(
                "forget on a hosted soul needs the Voice API key for that namepoint."
            )
        raw = await self.get()
        # get_soul returns the stored soul. Strip a leading banner only
        # when the remainder is the soul document.
        body = split_hosted_banner(raw)
        new_body, removed = apply_forget(body, criteria)
        if removed == 0:
            return {"removed": 0, "namepoint": self._soul}
        await self._replace_soul(new_body)
        self._cache = new_body
        return {"removed": removed, "namepoint": self._soul}

    async def _replace_soul(self, content: str) -> str:
        """Overwrite the hosted soul. Never calls ``delete_soul``.

        Contract, read from ``mcpaas-cf`` ``handler.ts`` ``write_soul``:
        when ``replace`` is true and ``content`` is set, the server
        does ``env.SOULS.put(soulName, replaceContent)`` and the soul
        name is lowercased. Voice keys may write only their own
        namepoint. A live hosted call was not opened.
        """
        args: dict[str, Any] = {
            "soul": self._soul,
            "replace": True,
            "content": content,
            "token": self._api_key,
        }
        transport = StreamableHttpTransport(
            url=self._mcp_url, headers=_MCPAAS_MODE_HEADER
        )
        try:
            async with Client(transport) as client:
                result = await client.call_tool("write_soul", args)
                return _first_text(result)
        except ToolError as e:
            raise self._wrap_tool_error(e, op="etch") from e

    async def get(self) -> str:
        """Read the soul's current state.

        In **local mode** (constructed via :meth:`from_file`) this reads the
        ``.fafm`` document straight off disk — no MCPaaS, no API key.
        Otherwise it reads via MCPaaS ``get_soul``.

        Returns the soul body as a string. Current MCPaaS ``get_soul``
        returns the stored text and does not add a banner. The local
        path returns the file's exact contents.
        """
        if self._local_path is not None:
            text = self._local_path.read_text(encoding="utf-8")
            self._cache = text
            return text
        # Sessionless MCP compat (2026-05-11): explicit transport with flexi header.
        transport = StreamableHttpTransport(url=self._mcp_url, headers=_MCPAAS_MODE_HEADER)
        try:
            async with Client(transport) as client:
                result = await client.call_tool("get_soul", {"soul": self._soul})
                text = _first_text(result)
                self._cache = text
                return text
        except ToolError as e:
            raise self._wrap_tool_error(e, op="recall") from e

    @classmethod
    def from_file(cls, path: str | Path, **kwargs: Any) -> FAFMemory:
        """Load a soul from a local ``.fafm`` file — no MCPaaS, no API key.

        The local-first companion to the MCPaaS-backed constructor. Reads a
        ``.fafm`` document straight off disk so you can inspect, test, or run
        recall against a soul offline — including souls written by other
        FAF-family tools (cross-vendor interop). Works with any profile
        (``voice`` or ``knowledge``).

        Pairs with :meth:`to_file` for a byte-identical roundtrip. Validate
        documents against the published JSON Schema (``schemas/fafm.schema.json``
        in the ``faf`` repo) for editor/CI DX.

        Examples
        --------
        >>> mem = FAFMemory.from_file("claude-code-wolfejam.fafm")
        >>> body = await mem.get()             # the file's soul body
        >>> recall = await mem.recall_for_prompt()  # not for instructions

        Parameters
        ----------
        path : str | Path
            Path to a ``.fafm`` document.
        **kwargs
            Forwarded to ``__init__`` (e.g. ``ledger=``, ``bus=``).

        Returns
        -------
        FAFMemory
            An instance in local mode — ``get()`` reads the file, not MCPaaS.
        """
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        # Lightweight soul-identifier extraction (no YAML dependency).
        m = re.search(r"(?m)^namepoint:\s*['\"]?(@[^'\"\n]+?)['\"]?\s*$", text)
        soul = m.group(1).strip() if m else p.stem
        return cls(soul, local_path=p, **kwargs)

    async def to_file(self, path: str | Path) -> Path:
        """Write the current soul body to a local ``.fafm`` file.

        The inverse of :meth:`from_file`. Fetches the soul (local or MCPaaS)
        and writes it to disk — handy for backups, inspection, or handing a
        soul to another FAF-family tool. Returns the written path.

        >>> path = await mem.to_file("backup.fafm")
        """
        p = Path(path)
        p.write_text(await self.get(), encoding="utf-8")
        return p

    # -- parsed accessors -------------------------------------------------
    # Typed views over the soul document. Local mode parses the file
    # directly (no await needed); MCPaaS mode reads the cache populated by a
    # prior `await get()`.

    def _parse_soul(self) -> dict[str, Any]:
        text = self._cache
        if text is None:
            if self._local_path is not None:
                text = self._local_path.read_text(encoding="utf-8")
                self._cache = text
            else:
                raise FAFRecallError(
                    "No soul loaded yet — call `await get()` first (MCPaaS mode), "
                    "or use `FAFMemory.from_file(path)` for local access."
                )
        # Strip the MCPaaS server preamble (everything before the first
        # `\n---\n`) if present, mirroring recall_for_prompt.
        sep = "\n---\n"
        if sep in text:
            text = text.split(sep, 1)[1]
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise FAFRecallError(f"Soul is not valid YAML: {e}") from e
        return doc if isinstance(doc, dict) else {}

    @property
    def profile(self) -> str:
        """The soul's profile — ``"voice"`` (default) or ``"knowledge"``.

        Parsed lazily from the soul document. Local mode reads the file;
        MCPaaS mode needs a prior ``await get()``.
        """
        return self._parse_soul().get("profile", "voice")

    @property
    def facts(self) -> list[Any]:
        """The soul's memory facts (parsed). Empty list if none.

        Items are whatever the document holds — a bare string, a
        ``{text, tags?}`` object, or a rich knowledge object — per the
        ``.fafm`` spec. Local mode parses the file directly; MCPaaS mode
        needs a prior ``await get()``.
        """
        memory = self._parse_soul().get("memory") or {}
        return memory.get("facts") or []

    @property
    def index(self) -> list[Any]:
        """The soul's top-level ``index`` (knowledge profile). Empty if none."""
        return self._parse_soul().get("index") or []

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
    # Paralinguistic markers
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
        entry, tags = self.format_paralinguistic_entry(
            marker_type, value, context=context
        )
        return await self.etch(entry, type="paralinguistic", tags=tags)

    def format_paralinguistic_entry(
        self,
        marker_type: str,
        value: str,
        *,
        context: str | None = None,
    ) -> tuple[str, list[str]]:
        """The entry text and tags, without writing."""
        entry_parts = [
            self.PARALINGUISTIC_PREFIX,
            f"{marker_type}: {value}",
        ]
        if context:
            entry_parts.append(f"— {context}")
        return " ".join(entry_parts), [self.PARALINGUISTIC_TAG, marker_type]

    # ----------------------------------------------------------------
    # Smart Merge Engine
    #
    # Promotes Scratchpad entries to permanent soul memory at session
    # end. Two strategies: a fast/free heuristic (default), and a
    # Grok-decides LLM call (opt-in).
    # ----------------------------------------------------------------

    MERGE_TAG = "merged"
    EPHEMERAL_PRIORITY = "ephemeral"
    SESSION_ONLY_TAG = "session-only"

    def _hold_session_only(
        self, entries: dict[str, ScratchpadEntry]
    ) -> tuple[dict[str, ScratchpadEntry], list]:
        """Pull session-only entries out before any strategy sees them."""
        eligible: dict[str, ScratchpadEntry] = {}
        held: list = []
        for key, entry in entries.items():
            if entry.tag == self.SESSION_ONLY_TAG:
                held.append((key, entry))
            else:
                eligible[key] = entry
        return eligible, held

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
          memory. Smarter, but costs tokens. An entry the model does
          not decide on stays ephemeral.

        - ``"merge_all"``: promote every entry regardless of priority.

        Entries tagged ``session-only`` are never promoted, on every
        strategy. They stay out of the soul.

        Clears the scratchpad after a successful merge and sets
        ``_merge_completed_this_session`` so the auto-merge shutdown
        callback can no-op.

        Returns
        -------
        dict
            ``{"promoted": int, "discarded": int, "strategy": str,
            "merged": int, "kept_ephemeral": int, "overall_notes": str | None}``

            ``promoted`` and ``discarded`` are preserved for backward
            compatibility. ``kept_ephemeral`` is a semantic alias for
            ``discarded``. ``merged`` is reserved for a future release
            (true scratchpad-internal consolidation); always 0 today.
            ``overall_notes`` is populated by the grok-decides strategy
            from the LLM's MergeResult; None for heuristic / merge_all.
        """
        entries = self._scratchpad.all_entries()
        if not entries:
            self._merge_completed_this_session = True
            return {
                "promoted": 0,
                "discarded": 0,
                "strategy": strategy,
                "merged": 0,
                "kept_ephemeral": 0,
                "overall_notes": None,
            }

        if strategy not in {"heuristic", "grok-decides", "merge_all"}:
            raise ValueError(
                f"Unknown merge strategy: {strategy!r}. "
                f"Valid: 'heuristic', 'grok-decides', 'merge_all'."
            )

        eligible, held = self._hold_session_only(entries)
        overall_notes: str | None = None
        if not eligible:
            promoted, discarded = [], []
        elif strategy == "heuristic":
            promoted, discarded = self._heuristic_split(eligible)
        elif strategy == "grok-decides":
            try:
                promoted, discarded, overall_notes = (
                    await self._grok_decides_split(eligible, chat_model=chat_model)
                )
            except Exception as e:
                raise FAFMergeError(
                    f"grok-decides strategy failed: {e}. Try strategy='heuristic'."
                ) from e
        else:
            promoted, discarded = list(eligible.items()), []

        # A strategy must not write a session-only entry even if it
        # returns one. Those facts never reach the permanent soul.
        safe: list = []
        for key, entry in promoted:
            if entry.tag == self.SESSION_ONLY_TAG:
                held.append((key, entry))
            else:
                safe.append((key, entry))
        promoted = safe
        discarded = list(discarded) + held

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
        self._merge_completed_this_session = True

        return {
            "promoted": len(promoted),
            "discarded": len(discarded),
            "strategy": strategy,
            "merged": 0,
            "kept_ephemeral": len(discarded),
            "overall_notes": overall_notes,
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
    ) -> tuple[list, list, str | None]:
        """Ask Grok which entries deserve permanent memory.

        Hits xAI's chat completions REST endpoint directly (same
        pattern as utils/transcribe.py) with structured outputs bound
        to ``MergeResult``'s JSON schema — guaranteed-valid response
        shape, no fallback parser needed.

        ``merge_into`` decisions are treated as ``promote`` for now;
        true scratchpad-internal consolidation lands in a future release.
        """
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "XAI_API_KEY not set — required for strategy='grok-decides'."
            )

        payload_entries, scratchpad_json = self._prepare_llm_input(entries)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(
                    soul_summary="No prior soul summary available.",
                    num_entries=len(payload_entries),
                    scratchpad_json=scratchpad_json,
                    existing_memories_json="[]",
                ),
            },
        ]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                XAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": chat_model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "MergeResult",
                            "strict": True,
                            "schema": MergeResult.model_json_schema(),
                        },
                    },
                    "temperature": 0.25,
                    "max_tokens": 4500,
                },
            )
            resp.raise_for_status()
            payload = resp.json()

        content = payload["choices"][0]["message"]["content"]
        result = MergeResult.model_validate_json(content)

        promoted: list[tuple[str, ScratchpadEntry]] = []
        discarded: list[tuple[str, ScratchpadEntry]] = []
        seen: set[str] = set()
        for dec in result.decisions:
            entry = entries.get(dec.entry_id)
            if entry is None:
                continue
            seen.add(dec.entry_id)
            if dec.action in ("promote", "merge_into"):
                promoted.append((dec.entry_id, entry))
            else:  # keep_ephemeral
                discarded.append((dec.entry_id, entry))

        # An entry the model did not decide on stays ephemeral.
        for key, entry in entries.items():
            if key not in seen:
                discarded.append((key, entry))

        return promoted, discarded, result.overall_notes

    @staticmethod
    def _prepare_llm_input(
        entries: dict[str, ScratchpadEntry],
    ) -> tuple[list[MergePayloadEntry], str]:
        """Convert internal Scratchpad entries to LLM wire shape + JSON.

        Maps the SDK's ``priority`` levels onto the LLM's
        ``ephemeral|standard|high|critical`` enum. ``"medium"`` (today's
        default) maps to ``"standard"``; any unknown value falls back to
        ``"standard"`` so the schema validator never rejects.
        """
        priority_map = {
            "ephemeral": "ephemeral",
            "low": "standard",
            "medium": "standard",
            "standard": "standard",
            "high": "high",
            "critical": "critical",
        }
        payload_entries: list[MergePayloadEntry] = []
        for key, e in entries.items():
            tags = [e.tag] if e.tag else []
            payload_entries.append(
                MergePayloadEntry(
                    id=key,
                    content=e.value,
                    priority=priority_map.get(e.priority, "standard"),
                    tags=tags,
                )
            )
        scratchpad_json = json.dumps(
            [m.model_dump(mode="json", exclude_none=True) for m in payload_entries],
            indent=2,
            ensure_ascii=False,
        )
        return payload_entries, scratchpad_json

    def attach_auto_merge(
        self,
        session: AgentSession,
        ctx: JobContext,
        *,
        strategy: str = "heuristic",
    ) -> None:
        """Register a shutdown callback that holds the scratchpad.

        It does not call ``merge()`` and it does not etch. A write happens
        only after the agent speaks the fact and the namepoint holder
        confirms, or when the holder asks ``merge_now`` to save the
        scratchpad. The callback logs status ``held``.

        Uses ``ctx.add_shutdown_callback`` (the load-bearing primitive on
        the LiveKit ``JobContext``) rather than ``session.on("close")``.
        ``session.on("close")`` is kept for observability only.

        Composes with explicit ``merge_now``: an async lock + two guard
        flags ensure the shutdown callback no-ops when the user already
        triggered a merge mid-conversation.

        Resets the per-session merge guard flags so reusing one
        ``FAFMemory`` across multiple sessions (the README pattern —
        module-scope ``mem`` + per-session ``entrypoint``) doesn't
        cause session 2+ to no-op when the prior session already finished.
        """
        self._merge_completed_this_session = False
        self._merge_in_progress = False

        async def _merge_on_shutdown() -> None:
            async with self._lock:
                if self._merge_completed_this_session:
                    return  # explicit merge_now already ran
                if self._merge_in_progress:
                    # Rare race with merge_now — wait briefly then re-check
                    await asyncio.sleep(0.8)
                    if self._merge_completed_this_session:
                        return
                self._merge_in_progress = True

            # strategy is accepted so existing callers keep working.
            # Shutdown does not merge. The holder confirms, or calls merge_now.
            _ = strategy
            try:
                await self.ledger.log_merge_attempt(
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="held",
                    promoted=0,
                    merged=0,
                    kept_ephemeral=len(self._scratchpad),
                    overall_notes=(
                        "Confirm-before-write: scratchpad held. "
                        "Nothing is etched until the holder confirms."
                    ),
                )
                self._merge_completed_this_session = True
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "shutdown hold failed (non-fatal): %s", exc
                )
                await self.ledger.log_merge_attempt(
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="partial_or_failed",
                    error=str(exc),
                )
            finally:
                self._merge_in_progress = False

        ctx.add_shutdown_callback(_merge_on_shutdown)

        # Keep on("close") for observability only — never put work here.
        @session.on("close")
        def _on_close(*_args: Any, **_kwargs: Any) -> None:
            logging.getLogger(__name__).debug(
                "[FAFMemory] Session %s closed — merge handled via shutdown callback",
                getattr(session, "id", "unknown"),
            )

    # ----------------------------------------------------------------
    # Cross-session resumption
    # ----------------------------------------------------------------

    #: Below this retry count, resumption is silent (no user-facing audio).
    _RESUMPTION_HIGH_SEVERITY_RETRY_THRESHOLD = 2
    #: Above this many failed entries on a single attempt, surface a message.
    _RESUMPTION_HIGH_SEVERITY_FAILED_ENTRIES = 5
    #: After this many retries, abandon the attempt with status="failed".
    _RESUMPTION_MAX_RETRIES = 3
    #: Default age window for picking up incomplete merges.
    _RESUMPTION_MAX_AGE_HOURS = 72
    #: Warm message used when high-severity is detected.
    _RESUMPTION_USER_MESSAGE = (
        "I had a couple of memories from last time that didn't fully save. "
        "I'm catching up on them now — one moment."
    )

    async def on_session_start(
        self,
        session: AgentSession,
        *,
        max_age_hours: int | None = None,
        strategy: str = "heuristic",
    ) -> dict[str, Any]:
        """Resume any incomplete merges from prior sessions.

        Default behavior is **silent idempotent retry** — 95% of cases
        recover invisibly. The user only hears about it when a retry
        looks high-severity (retry_count ≥ 2 or many failed entries).

        Call this immediately after ``session.start(...)`` in your
        agent entrypoint. Safe to call when there's nothing to resume
        (no-op).

        Parameters
        ----------
        session : AgentSession
            The live LiveKit session — used to optionally emit a warm
            user-facing message when severity is high.
        max_age_hours : int, optional
            Override the default 72h window. Older incomplete attempts
            are skipped silently.
        strategy : str, default "heuristic"
            Merge strategy to use for the retry. Heuristic by default
            for cost; pass ``"grok-decides"`` if the user is paying
            for retries to be smart.

        Returns
        -------
        dict
            Summary: ``{"resumed": int, "succeeded": int, "failed": int,
            "abandoned": int, "user_messaged": bool}``.
        """
        max_age = max_age_hours or self._RESUMPTION_MAX_AGE_HOURS
        incomplete = await self.ledger.get_incomplete_merges(
            soul=self._soul, max_age_hours=max_age
        )

        if not incomplete:
            return {
                "resumed": 0,
                "succeeded": 0,
                "failed": 0,
                "abandoned": 0,
                "user_messaged": False,
            }

        high_severity = any(
            m.retry_count >= self._RESUMPTION_HIGH_SEVERITY_RETRY_THRESHOLD
            or len(m.failed_entry_ids)
            > self._RESUMPTION_HIGH_SEVERITY_FAILED_ENTRIES
            for m in incomplete
        )

        user_messaged = False
        if high_severity:
            try:
                await session.say(self._RESUMPTION_USER_MESSAGE)
                user_messaged = True
            except Exception as exc:  # noqa: BLE001
                # Don't let a failed say() block the actual recovery.
                logging.getLogger(__name__).warning(
                    "resumption user-message failed (non-fatal): %s", exc
                )

        succeeded = 0
        failed = 0
        abandoned = 0
        for attempt in incomplete:
            if attempt.retry_count >= self._RESUMPTION_MAX_RETRIES:
                # Final abandonment — log "failed" with same id, move on.
                await self.ledger.log_merge_attempt(
                    merge_attempt_id=attempt.merge_attempt_id,
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="failed",
                    retry_count=attempt.retry_count,
                    error="abandoned: max retries exceeded",
                )
                abandoned += 1
                continue

            await self.ledger.mark_merge_resumed(
                merge_attempt_id=attempt.merge_attempt_id
            )
            try:
                result = await self.merge(strategy=strategy)
                await self.ledger.log_merge_attempt(
                    merge_attempt_id=attempt.merge_attempt_id,
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="completed",
                    promoted=result.get("promoted", 0),
                    merged=result.get("merged", 0),
                    kept_ephemeral=result.get("kept_ephemeral", 0),
                    overall_notes=result.get("overall_notes"),
                    retry_count=attempt.retry_count + 1,
                )
                succeeded += 1
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "resumption retry failed (non-fatal): %s", exc
                )
                await self.ledger.log_merge_attempt(
                    merge_attempt_id=attempt.merge_attempt_id,
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="partial_or_failed",
                    error=str(exc),
                    retry_count=attempt.retry_count + 1,
                )
                failed += 1

        summary = {
            "resumed": len(incomplete),
            "succeeded": succeeded,
            "failed": failed,
            "abandoned": abandoned,
            "user_messaged": user_messaged,
        }
        await self._bus.emit_session_resumed(summary)
        return summary

    async def recall_for_prompt(
        self,
        *,
        header: str = "What you know about this user from prior sessions",
        empty_message: str = (
            "This is your first session with this user. "
            "Use the etch_memory tool to save facts WHEN THE USER GIVES "
            "YOU SUBSTANCE TO SAVE — never on their meta-statement of "
            "intent. If the user says 'remember this' without saying "
            "what, ask 'What would you like me to remember?' and wait "
            "for the actual content."
        ),
    ) -> str:
        """Return the soul body for a later recall, not for ``instructions``.

        Do not call this at session open, and do not place the return
        value in ``session.instructions``. The open path is the standing
        string. The long tail is the ``recall_memory`` tool on a later
        turn, and only when tools are enabled. The first reply should
        not wait on this call.

        Fetches the live soul body and wraps it with a header. When the
        soul is empty or the read fails, returns ``empty_message`` and
        does not raise.

        Parameters
        ----------
        header : str
            Title text rendered above the soul body. Tune for tone if
            you want a warmer or colder open.
        empty_message : str
            Returned verbatim when the soul has no content yet.

        Returns
        -------
        str
            Either ``"<header>:\\n<body>"`` or ``empty_message``.
            Not for ``instructions``.
        """
        try:
            body = await self.get()
        except FAFRecallError:
            return empty_message
        except Exception:  # noqa: BLE001
            # Read failures (network, etc.) must not block the agent
            # from starting. Degrade silently to the empty path.
            return empty_message

        # Strip MCPaaS server preamble (everything before the first
        # ``\n---\n`` separator) so only the soul body lands in the
        # prompt. If no separator is present, use the whole text.
        sep = "\n---\n"
        if sep in body:
            body = body.split(sep, 1)[1]

        body = body.strip()
        if not body:
            return empty_message

        return f"{header}:\n{body}"

    async def paralinguistic_summary(self, *, max_recent: int = 5) -> str:
        """Build a one-line synopsis of recent paralinguistic markers.

        For a later recall. Do not place it in ``session.instructions``.
        The open path stays the standing string.

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

    def tools(self, session: AgentSession) -> list:
        """Return LiveKit `@function_tool` wrappers for the agent.

        Returns four tools: ``etch_memory`` + ``recall_memory`` +
        ``note_paralinguistic`` + ``merge_now``. ``confirm_memory`` is
        not among them. ``forget`` is not among them. The SDK confirms
        from the next final user transcript. Pass the list to the Agent
        ``tools=`` argument.

        ``session`` is required because the confirm listener and
        ``merge_now``'s spoken hold use it. The realtime stream goes
        silent during a merge otherwise.
        """
        from grok_faf_voice.tools import (
            make_etch_tool,
            make_merge_tool,
            make_paralinguistic_tool,
            make_recall_tool,
        )

        self.attach_confirm(session)
        return [
            make_etch_tool(self),
            make_recall_tool(self),
            make_paralinguistic_tool(self),
            make_merge_tool(self, session),
        ]


def _write_text_owner_only(path: Path, text: str) -> None:
    """Replace ``path`` via a temp file in the same directory, mode 0600.

    ``Path.write_text`` truncates in place, so an interrupted write
    leaves a partial soul. ``os.replace`` is atomic on the same
    filesystem. Personal memory is owner-read/write only.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def _same_namepoint(left: str, right: str) -> bool:
    """Match a namepoint to the bound soul, ignoring ``@`` and case."""

    def norm(value: str) -> str:
        text = value.strip()
        if text.startswith("@"):
            text = text[1:]
        return text.casefold()

    return norm(left) == norm(right)


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
