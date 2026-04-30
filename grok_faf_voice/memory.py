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
from typing import TYPE_CHECKING, Any

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from grok_faf_voice._merge_models import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    MergePayloadEntry,
    MergeResult,
)
from grok_faf_voice.context_bus import BusEvent, ContextBus
from grok_faf_voice.ledger import NullVoiceSessionLedger, VoiceSessionLedger
from grok_faf_voice.scratchpad import Scratchpad, ScratchpadEntry

if TYPE_CHECKING:
    from livekit.agents import AgentSession, JobContext

MCPAAS_URL = "https://mcpaas.live/mcp"
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
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
- For etch_memory / recall_memory / note_paralinguistic: ALWAYS speak a short
  natural bridge FIRST ("Got it.", "Noted.", "Let me check...") then call the
  tool. Never go silent during tool execution.
- For merge_now: say "Give me just a moment while I consolidate..." before
  starting — it can take several seconds.

ETCH DISCIPLINE (follow exactly):
- etch_memory must receive the ACTUAL fact or detail, never the user's
  intent to remember. If the user says "I want to make a memory" or
  "remember this" without saying WHAT, ask "What would you like me
  to remember?" and WAIT for the substance. THEN etch only the substance.
- Never etch meta-statements like "user wants to make a memory" or
  "user said something" — those pollute the soul.
- One fact = one etch. Don't fire etch_memory before the user has
  finished giving you the content.
""".strip()

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
                turn_detection={"type": "server_vad", "threshold": 0.85,
                                "silence_duration_ms": 500,
                                "prefix_padding_ms": 333},
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
    ) -> None:
        self._soul = soul
        self._api_key = api_key or os.environ.get("MCPAAS_API_KEY")
        self._mcp_url = mcp_url
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

        try:
            async with Client(self._mcp_url) as client:
                result = await client.call_tool("write_soul", args)
                text = _first_text(result)
        except ToolError as e:
            raise self._wrap_tool_error(e, op="etch") from e

        await self._bus.emit(
            BusEvent.SOUL_UPDATED,
            {"soul": self._soul, "type": type, "tags": tags or []},
        )
        return text

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
    # Smart Merge Engine
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

        overall_notes: str | None = None
        if strategy == "heuristic":
            promoted, discarded = self._heuristic_split(entries)
        elif strategy == "grok-decides":
            try:
                promoted, discarded, overall_notes = (
                    await self._grok_decides_split(entries, chat_model=chat_model)
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

        # Conservative fallback for any entry the model didn't decide on.
        for key, entry in entries.items():
            if key not in seen:
                promoted.append((key, entry))

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
        """Register an awaitable shutdown callback that runs ``merge()``.

        Uses ``ctx.add_shutdown_callback`` (the load-bearing primitive on
        the LiveKit ``JobContext``) rather than ``session.on("close")`` —
        the worker awaits shutdown callbacks up to
        ``shutdown_process_timeout`` (~10s default), so a multi-second
        merge can finish cleanly on every termination path (graceful
        close, user disconnect, room destroy, worker drain).

        Composes with explicit ``merge_now``: an async lock + two guard
        flags ensure the shutdown callback no-ops when the user already
        triggered a merge mid-conversation. ``session.on("close")`` is
        kept for observability only.

        Every attempt is recorded in the configured ledger (success and
        failure paths). Failures inside the merge are caught and logged
        — never re-raised, so shutdown completes cleanly.

        Resets the per-session merge guard flags so reusing one
        ``FAFMemory`` across multiple sessions (the README pattern —
        module-scope ``mem`` + per-session ``entrypoint``) doesn't
        cause session 2+ to no-op when the prior session already merged.
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

            await self._bus.emit_merge_starting(reason="auto-merge on shutdown")
            try:
                result = await self.merge(strategy=strategy)
                await self.ledger.log_merge_attempt(
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="completed",
                    promoted=result.get("promoted", 0),
                    merged=result.get("merged", 0),
                    kept_ephemeral=result.get("kept_ephemeral", 0),
                    overall_notes=result.get("overall_notes"),
                )
                await self._bus.emit_merge_completed(result=result)
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "auto-merge failed (non-fatal): %s", exc
                )
                await self.ledger.log_merge_attempt(
                    session_id=getattr(session, "id", "unknown"),
                    soul=self._soul,
                    status="partial_or_failed",
                    error=str(exc),
                )
                await self._bus.emit(
                    BusEvent.MERGE_COMPLETED,
                    {"error": str(exc), "status": "partial_or_failed"},
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
        """Return a formatted recall block ready for system-prompt injection.

        Fetches the live soul body and wraps it with a header so the
        model treats it as prior context. When the soul is empty or the
        read fails (no API key, network blip, ledger gap), returns the
        ``empty_message`` so the agent still opens cleanly — never raises.

        The caller composes this into the Agent's instructions::

            agent = Agent(
                instructions=(
                    f"{faf.system_prompt()}\\n\\n"
                    f"{await mem.recall_for_prompt()}\\n\\n"
                    f"{LATENCY_BRIDGE_INSTRUCTIONS}"
                ),
                tools=mem.tools(session),
            )

        This is the bridge between persistent soul state and the
        in-context state the realtime model actually sees. Without it,
        the SDK persists memories perfectly but the agent opens each
        session blind.

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
            Always safe to inline into instructions.
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

    def tools(self, session: AgentSession) -> list:
        """Return LiveKit `@function_tool` wrappers for the agent.

        Returns four tools: ``etch_memory`` + ``recall_memory`` +
        ``note_paralinguistic`` + ``merge_now``. Pass into the Agent
        (or AgentSession) ``tools=`` list to expose memory commands
        to the voice agent.

        ``session`` is required because ``merge_now`` uses an explicit
        verbal hold (``await session.say("Give me just a moment...")``)
        to mask the multi-second merge latency — the realtime stream
        goes silent during tool execution otherwise.
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
            make_merge_tool(self, session),
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
