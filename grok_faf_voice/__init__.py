"""grok-faf-voice — Persistent memory for Grok Voice. LiveKit enabled.

Two first-class siblings + the Scratchpad primitive:

- ``FAFContext`` — static project DNA, read once per session.
  Loads `.faf` (`application/vnd.faf+yaml`).
- ``FAFMemory``  — live voice memory layer. Reads/writes a soul on
  MCPaaS via the MCP protocol. Loads `.fafm`
  (`application/vnd.fafm+yaml` planned).
- ``Scratchpad`` — in-session ephemeral key/value store, composed
  by ``FAFMemory``.

Stateless by default. Memory is a tool, not a baseline.
"""

from grok_faf_voice.context import FAFContext
from grok_faf_voice.ledger import (
    InMemoryVoiceSessionLedger,
    NullVoiceSessionLedger,
    VoiceSessionLedger,
)
from grok_faf_voice.memory import (
    LATENCY_BRIDGE_INSTRUCTIONS,
    FAFAuthRequiredError,
    FAFEtchError,
    FAFMemory,
    FAFMergeError,
    FAFRecallError,
)
from grok_faf_voice.scratchpad import Scratchpad, ScratchpadEntry

__version__ = "0.0.7"
__all__ = [
    "FAFAuthRequiredError",
    "FAFContext",
    "FAFEtchError",
    "FAFMemory",
    "FAFMergeError",
    "FAFRecallError",
    "InMemoryVoiceSessionLedger",
    "LATENCY_BRIDGE_INSTRUCTIONS",
    "NullVoiceSessionLedger",
    "Scratchpad",
    "ScratchpadEntry",
    "VoiceSessionLedger",
]
