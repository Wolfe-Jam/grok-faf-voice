"""FAFMemory — live voice memory layer for Grok Voice agents.

The Voice Memory Layer that the static `.faf` (FAFContext) can't
provide: audio-native, session-persistent primitives. Voice Scratchpad
(live in-call memory), Paralinguistic Tags, Smart Merge Engine, Voice
Session Ledger, Real-time Context Bus.

Loads a `.fafm` file (local path) or MCPaaS slug. `.fafm` carries
voice-memory-specific schema, extending `application/vnd.faf+yaml`
(`application/vnd.fafm+yaml` planned).

Skeleton at Gate 1. Real surface ships across Gates 2-6 of the build
plan. The class is intentionally importable from v0.0.1 so the public
API surface is stable from day one.
"""

from __future__ import annotations


class FAFMemory:
    """Live voice memory for a Grok Voice agent.

    Skeleton at Gate 1. Real surface ships across Gates 2–6:

    - Gate 2: Voice Scratchpad (in-call structured memory)
    - Gate 3: Paralinguistic Tags (.fafm schema extension)
    - Gate 4: Smart Merge Engine (scratchpad → .fafm at session end)
    - Gate 5: Voice Session Ledger (cross-call welcome-back)
    - Gate 6: Real-time Context Bus (sub-80ms mid-stream context mutation)

    Parameters
    ----------
    arg : str
        Either a local `.fafm` file path (must exist on disk) or a
        MCPaaS slug. Auto-detection follows the same pattern as
        FAFContext.

    Examples
    --------
    Future shape (Gate 2+)::

        ctx = FAFContext("project.faf")
        mem = FAFMemory("project.fafm")

        session = AgentSession(
            llm=xai.realtime.RealtimeModel(voice="Ara"),
            instructions=ctx.system_prompt(),
        )
        mem.attach(session)  # wires Scratchpad + etch + merge
    """

    def __init__(self, arg: str):
        self._arg = arg
