"""FAFContext — static project DNA loader for Grok Voice agents.

Loads a `.faf` file (local path) or fetches by slug from the MCPaaS
read endpoint (`https://mcpaas.live/raw/{slug}`). Returns a system
prompt string ready to inject into a LiveKit AgentSession.

This is the static layer — read once at session start. For live
session memory (Scratchpad, paralinguistic, etc.), see FAFMemory.
"""

from __future__ import annotations

from pathlib import Path

import httpx

MCPAAS_RAW_URL = "https://mcpaas.live/raw/{slug}"


class FAFContext:
    """Static project DNA for a Grok Voice agent.

    Loads a `.faf` from local disk or MCPaaS, exposes it as a system
    prompt string. Stateless by design — memory accretion is FAFMemory's
    job, not this class's.

    Parameters
    ----------
    arg : str
        Either a local `.faf` file path (must exist on disk) or a
        MCPaaS slug. Auto-detected: if the string is a path that
        exists locally, it is read from disk; otherwise it is fetched
        from `mcpaas.live/raw/{slug}`.

    Examples
    --------
    Local file::

        faf = FAFContext("project.faf")
        session = AgentSession(
            llm=xai.realtime.RealtimeModel(voice="Ara"),
            instructions=faf.system_prompt(),
        )

    MCPaaS slug::

        faf = FAFContext("my-project")  # fetches mcpaas.live/raw/my-project
        prompt = faf.system_prompt()
    """

    def __init__(self, arg: str):
        self._arg = arg
        self._content: str | None = None

    def load(self) -> str:
        """Load and cache the `.faf` content. Idempotent."""
        if self._content is not None:
            return self._content

        path = Path(self._arg)
        if path.exists() and path.is_file():
            self._content = path.read_text()
        else:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(MCPAAS_RAW_URL.format(slug=self._arg))
                resp.raise_for_status()
                self._content = resp.text

        return self._content

    def system_prompt(self) -> str:
        """Return the `.faf` content wrapped as a system prompt string.

        The model parses the YAML directly — no rendering layer that
        could drift from the source. Wraps with a brief instruction
        so the model knows what it's looking at.
        """
        content = self.load()
        return (
            "You have persistent project context loaded as a .faf "
            "(Foundational AI-context Format). Use this context "
            "naturally. Never ask the user to re-explain what is "
            "already known.\n\n"
            f"--- BEGIN .faf ---\n{content}\n--- END .faf ---"
        )
