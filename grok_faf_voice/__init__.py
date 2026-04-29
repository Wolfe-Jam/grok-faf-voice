"""grok-faf-voice — Persistent memory for Grok Voice. LiveKit enabled.

Two first-class siblings + the Scratchpad primitive:

- ``FAFContext`` — static project DNA, read once per session.
  Loads `.faf` (`application/vnd.faf+yaml`).
- ``FAFMemory``  — live voice memory. Reads/writes a soul on MCPaaS
  via the MCP protocol. Loads `.fafm` (`application/vnd.fafm+yaml`
  planned).
- ``Scratchpad`` — in-session ephemeral key/value store, composed
  by ``FAFMemory``.

Plus a Context Bus for async pub/sub over voice-memory events
(``ContextBus`` + ``BusEvent`` + ``BusEventPayload``).

Stateless by default. Memory is a tool, not a baseline.
"""

from grok_faf_voice.context import FAFContext
from grok_faf_voice.context_bus import BusEvent, BusEventPayload, ContextBus
from grok_faf_voice.ledger import (
    InMemoryVoiceSessionLedger,
    MergeAttempt,
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
from grok_faf_voice.tools import enable_global_tool_bus

__version__ = "0.1.1"
__all__ = [
    "BusEvent",
    "BusEventPayload",
    "ContextBus",
    "FAFAuthRequiredError",
    "FAFContext",
    "FAFEtchError",
    "FAFMemory",
    "FAFMergeError",
    "FAFRecallError",
    "InMemoryVoiceSessionLedger",
    "LATENCY_BRIDGE_INSTRUCTIONS",
    "MergeAttempt",
    "NullVoiceSessionLedger",
    "Scratchpad",
    "ScratchpadEntry",
    "VoiceSessionLedger",
    "enable_global_tool_bus",
]
