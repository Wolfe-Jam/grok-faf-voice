"""VoiceAgent — the zero-config composer for grok-faf-voice.

The two-line shape:

    from grok_faf_voice import VoiceAgent
    VoiceAgent().run()

That is the entire setup for the 10,000 voice consumers. First run
provisions an anonymous identity silently (key + namepoint) via
mcpaas.live and persists it at ``~/.grok-faf-voice/identity.json``.
Subsequent runs load the identity and the agent picks up every
session knowing what was etched in past ones.

The architects who want explicit control still have ``FAFMemory``,
``FAFContext``, and raw LiveKit ``AgentServer`` underneath — this
class composes them, it does not replace them. Pass ``api_key=`` and
``namepoint=`` to opt out of zero-config.

Identity precedence (highest first):
1. Constructor ``api_key=`` + ``namepoint=`` (both must be set)
2. Env vars ``MCPAAS_API_KEY`` + ``FAF_SOUL`` (both must be set)
3. Local ``~/.grok-faf-voice/identity.json`` (loaded if exists)
4. ``POST /api/voice/issue/anonymous`` (mints fresh, persists locally)

See ``grok-faf-voice-dev-is-user.md`` and
``grok-faf-voice-no-mode-naming.md`` for the doctrine this serves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from dotenv import load_dotenv

from grok_faf_voice.context import FAFContext
from grok_faf_voice.ledger import InMemoryVoiceSessionLedger, VoiceSessionLedger
from grok_faf_voice.memory import LATENCY_BRIDGE_INSTRUCTIONS, FAFMemory
from grok_faf_voice.tools import enable_global_tool_bus

if TYPE_CHECKING:
    from livekit import agents

log = logging.getLogger("grok_faf_voice.agent")

# xAI realtime voices — validated at construction so typos surface early.
SUPPORTED_VOICES: tuple[str, ...] = ("Ara", "Eve", "Leo", "Rex", "Sal")

# Where the anonymous identity is persisted locally. The SDK is the
# system of record for anonymous identities — server has no recovery
# flow without an email.
DEFAULT_IDENTITY_PATH = Path.home() / ".grok-faf-voice" / "identity.json"

# mcpaas-cf endpoints. Override via constructor kwargs only when running
# against a private mcpaas deployment (rare).
DEFAULT_ANONYMOUS_ISSUE_URL = "https://mcpaas.live/api/voice/issue/anonymous"
DEFAULT_MCP_URL = "https://mcpaas.live/mcp"

# Schema version for the identity file. Bumped if the on-disk shape
# changes; current loader rejects unknown versions with a clear error.
IDENTITY_SCHEMA_VERSION = 1


class VoiceAgentConfigError(RuntimeError):
    """Raised when VoiceAgent cannot start because required config is missing.

    Always carries an actionable message — what's missing and where to
    get it. Never a low-level traceback as the only signal.
    """


def _load_identity(path: Path) -> tuple[str, str] | None:
    """Load (namepoint, api_key) from an identity file, or None if absent.

    Returns None if the file doesn't exist. Raises VoiceAgentConfigError
    if the file exists but is malformed or has an unsupported schema —
    we'd rather fail loud than silently re-provision and orphan the
    user's existing memory.
    """
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceAgentConfigError(
            f"Identity file at {path} is unreadable ({exc}). "
            f"Delete it to re-provision a fresh anonymous identity, "
            f"or restore from backup."
        ) from exc

    version = data.get("version")
    if version != IDENTITY_SCHEMA_VERSION:
        raise VoiceAgentConfigError(
            f"Identity file at {path} has unsupported schema version "
            f"{version!r} (expected {IDENTITY_SCHEMA_VERSION}). "
            f"Upgrade grok-faf-voice or delete the file to re-provision."
        )

    namepoint = data.get("namepoint")
    api_key = data.get("api_key")
    if not (isinstance(namepoint, str) and isinstance(api_key, str)):
        raise VoiceAgentConfigError(
            f"Identity file at {path} is missing 'namepoint' or "
            f"'api_key'. Delete it to re-provision."
        )
    return namepoint, api_key


def _persist_identity(path: Path, namepoint: str, api_key: str) -> None:
    """Write identity to disk with restrictive permissions.

    Creates the parent directory if missing. File is written 0600 so
    other users on the system cannot read the API key.
    """
    from datetime import datetime, timezone

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": IDENTITY_SCHEMA_VERSION,
        "namepoint": namepoint,
        "api_key": api_key,
        "created": datetime.now(timezone.utc).isoformat(),
        "source": "anonymous-issue",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        # Windows + some FUSE filesystems don't honor chmod — best effort.
        pass


def _provision_anonymous(issue_url: str) -> tuple[str, str]:
    """POST to the anonymous issue endpoint, return (namepoint, api_key).

    Raises VoiceAgentConfigError on any non-200 — the user can't talk
    to anything until they have an identity, so loud failure is correct.
    """
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(issue_url, json={})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise VoiceAgentConfigError(
            f"Could not provision anonymous identity from {issue_url} "
            f"({exc}). Check your network. To run with an explicit "
            f"key, claim one at https://mcpaas.live/voice/setup and "
            f"set MCPAAS_API_KEY + FAF_SOUL env vars."
        ) from exc

    namepoint = data.get("namepoint")
    api_key = data.get("key")
    if not (isinstance(namepoint, str) and isinstance(api_key, str)):
        raise VoiceAgentConfigError(
            f"Anonymous issue endpoint returned an unexpected shape: "
            f"{data!r}. This is a server-side bug — please report at "
            f"https://github.com/Wolfe-Jam/grok-faf-voice/issues"
        )
    return namepoint, api_key


def _resolve_identity(
    *,
    api_key: str | None,
    namepoint: str | None,
    identity_path: Path,
    anonymous_issue_url: str,
) -> tuple[str, str]:
    """Walk the precedence chain to resolve (namepoint, api_key)."""
    # 1. Explicit kwargs
    if api_key is not None and namepoint is not None:
        return namepoint, api_key

    # 2. Env vars
    env_key = os.environ.get("MCPAAS_API_KEY")
    env_namepoint = os.environ.get("FAF_SOUL")
    if env_key and env_namepoint:
        return env_namepoint, env_key

    # 3. Local identity file
    loaded = _load_identity(identity_path)
    if loaded is not None:
        return loaded

    # 4. Anonymous provisioning
    namepoint, api_key = _provision_anonymous(anonymous_issue_url)
    _persist_identity(identity_path, namepoint, api_key)
    return namepoint, api_key


def _maybe_load_faf_context(spec: str | bool) -> FAFContext | None:
    """Resolve project_faf= constructor arg into a FAFContext or None.

    - False           → no FAFContext
    - "auto"          → look for ./project.faf; if missing, no context
    - any other str   → treat as path or MCPaaS slug (FAFContext handles)

    Broken FAF files are silently skipped (DEBUG-logged) — we don't
    want a malformed project.faf to block the agent from starting.
    """
    if spec is False:
        return None

    if spec == "auto":
        if not Path("project.faf").exists():
            return None
        spec = "project.faf"

    if not isinstance(spec, str):
        # True or other non-str — invalid, treat as no context
        return None

    try:
        ctx = FAFContext(spec)
        ctx.load()  # eagerly validate
        return ctx
    except Exception as exc:  # noqa: BLE001 — we deliberately swallow
        log.debug("Skipping FAFContext(%r): %s", spec, exc)
        return None


class VoiceAgent:
    """Zero-config voice agent with cross-session memory.

    The two-line shape::

        from grok_faf_voice import VoiceAgent
        VoiceAgent().run()

    First run provisions an anonymous identity silently (key + namepoint)
    via mcpaas.live and persists it at ``~/.grok-faf-voice/identity.json``.
    Subsequent runs load the identity — the agent opens every session
    already remembering what was etched in past ones.

    The minimum env requirement is ``XAI_API_KEY`` (xAI realtime is the
    voice provider). LiveKit cloud env vars (``LIVEKIT_*``) are only
    needed if you deploy to LiveKit; ``console`` mode runs locally with
    just the xAI key.

    Parameters
    ----------
    api_key
        MCPaaS Voice API key. If both this and ``namepoint`` are set,
        they take precedence over env vars and any local identity file.
        Leave as ``None`` for zero-config (auto-provisioning).
    namepoint
        The soul name (memory destination). See ``api_key``.
    voice
        Which xAI realtime voice. One of ``"Ara"``, ``"Eve"``, ``"Leo"``,
        ``"Rex"``, ``"Sal"``. Default: ``"Ara"``.
    project_faf
        Static project DNA. ``"auto"`` (default) looks for
        ``project.faf`` in the current directory and loads it if found.
        ``False`` skips entirely. Any other string is treated as a path
        or MCPaaS slug (see :class:`FAFContext`).
    instructions
        Extra system prompt appended after FAFContext + recall + the
        latency-bridge rules. Leave as ``None`` for the default agent
        persona.
    identity_path
        Where to persist the anonymous identity. Defaults to
        ``~/.grok-faf-voice/identity.json``. File is written 0600.
    ledger
        Voice session ledger. Defaults to in-memory (lasts the
        process). Pass a persistent implementation for production.
    mcp_url
        MCP endpoint for the FAFMemory layer. Override only when
        running against a private mcpaas deployment.
    anonymous_issue_url
        Where to POST when no identity exists. Override only when
        running against a private mcpaas deployment.
    voice_name
        Optional friendly name for the assistant in conversation
        bookkeeping. Currently unused; reserved for v0.2 Identity.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        namepoint: str | None = None,
        voice: str = "Ara",
        project_faf: str | bool = "auto",
        instructions: str | None = None,
        identity_path: str | Path | None = None,
        ledger: VoiceSessionLedger | None = None,
        mcp_url: str = DEFAULT_MCP_URL,
        anonymous_issue_url: str = DEFAULT_ANONYMOUS_ISSUE_URL,
        voice_name: str | None = None,
    ) -> None:
        if voice not in SUPPORTED_VOICES:
            raise VoiceAgentConfigError(
                f"voice={voice!r} is not a supported xAI realtime voice. "
                f"Choose one of: {', '.join(SUPPORTED_VOICES)}."
            )
        if (api_key is None) ^ (namepoint is None):
            raise VoiceAgentConfigError(
                "VoiceAgent: api_key and namepoint must both be set or "
                "both be None. Pass both for explicit identity, or "
                "neither for zero-config auto-provisioning."
            )

        self._api_key_arg = api_key
        self._namepoint_arg = namepoint
        self._voice = voice
        self._project_faf = project_faf
        self._extra_instructions = instructions
        self._identity_path = (
            Path(identity_path) if identity_path is not None
            else DEFAULT_IDENTITY_PATH
        )
        self._ledger = ledger
        self._mcp_url = mcp_url
        self._anonymous_issue_url = anonymous_issue_url
        self._voice_name = voice_name

    def run(self) -> None:
        """Block-and-run the agent.

        Reads ``sys.argv`` for LiveKit CLI subcommands —
        ``console`` / ``dev`` / ``start`` all flow through. The
        ``console`` subcommand runs the agent locally without any
        LiveKit cloud — only ``XAI_API_KEY`` is required.

        Equivalent to running a hand-wired LiveKit ``AgentServer``
        entrypoint, minus the wiring.
        """
        # load_dotenv before anything reads env so a .env file in CWD
        # contributes XAI_API_KEY / MCPAAS_API_KEY / etc.
        load_dotenv()

        # Validate required env early — surface a clear actionable error
        # before LiveKit/xAI wires anything up.
        if not os.environ.get("XAI_API_KEY"):
            raise VoiceAgentConfigError(
                "XAI_API_KEY env var required — get a key at "
                "https://x.ai/api and set it in your environment "
                "or in a .env file in the current directory."
            )

        # Identity resolution (this is where anonymous provisioning happens).
        existed_before = self._identity_path.exists()
        namepoint, resolved_api_key = _resolve_identity(
            api_key=self._api_key_arg,
            namepoint=self._namepoint_arg,
            identity_path=self._identity_path,
            anonymous_issue_url=self._anonymous_issue_url,
        )

        # If kwargs/env didn't supply identity AND there was no prior
        # identity file, we just provisioned a fresh anonymous identity.
        # Surface ONE quiet line so a confused dev whose file was deleted
        # knows their prior memory is orphaned. Silent on every other path.
        if (
            self._api_key_arg is None
            and self._namepoint_arg is None
            and not (os.environ.get("MCPAAS_API_KEY") and os.environ.get("FAF_SOUL"))
            and not existed_before
        ):
            print(
                f"grok-faf-voice: provisioned anonymous identity "
                f"({namepoint}) → {self._identity_path}",
                file=sys.stderr,
            )

        self._launch(namepoint=namepoint, api_key=resolved_api_key)

    async def run_async(self) -> None:
        """Async variant of :meth:`run` for embedding in existing apps.

        Note: LiveKit's ``agents.cli.run_app`` is the canonical entry
        and is itself synchronous (it owns the asyncio loop). This
        method is a convenience that runs the entire setup in a thread
        executor so it doesn't block the caller's loop.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.run)

    # --- Internal: build LiveKit AgentServer with all the wiring --------

    def _launch(self, *, namepoint: str, api_key: str) -> None:
        """Construct the LiveKit AgentServer and hand off to the CLI.

        Imports livekit lazily here so import-time cost stays low for
        users who never call .run() (e.g. tests that only construct
        the object and inspect it).
        """
        from livekit import agents
        from livekit.agents import Agent, AgentServer, AgentSession
        from livekit.plugins import xai

        faf = _maybe_load_faf_context(self._project_faf)
        mem = FAFMemory(
            soul=namepoint,
            api_key=api_key,
            mcp_url=self._mcp_url,
            ledger=self._ledger or InMemoryVoiceSessionLedger(),
        )

        voice = self._voice
        extra = self._extra_instructions
        agent_self = self  # noqa: F841 — captured by closure for future hooks

        server = AgentServer()

        @server.rtc_session()
        async def entrypoint(ctx: agents.JobContext) -> None:
            session = AgentSession(
                llm=xai.realtime.RealtimeModel(
                    voice=voice,
                    turn_detection={
                        "type": "server_vad",
                        "threshold": 0.85,
                        "silence_duration_ms": 500,
                        "prefix_padding_ms": 333,
                    },
                ),
            )

            prior_context = await mem.recall_for_prompt()

            instructions_parts: list[str] = []
            if faf is not None:
                instructions_parts.append(faf.system_prompt())
            instructions_parts.append(prior_context)
            if extra is not None:
                instructions_parts.append(extra)
            instructions_parts.append(LATENCY_BRIDGE_INSTRUCTIONS)
            instructions = "\n\n".join(p for p in instructions_parts if p)

            agent = Agent(
                instructions=instructions,
                tools=mem.tools(session),
            )

            await mem.start_bus()
            enable_global_tool_bus(mem, agent)
            mem.attach_auto_merge(session, ctx, strategy="grok-decides")

            await session.start(room=ctx.room, agent=agent)
            await mem.on_session_start(session)

        # Hand off to LiveKit CLI — reads sys.argv for the subcommand
        # (console / dev / start). This call blocks until the user
        # exits the CLI.
        agents.cli.run_app(server)
