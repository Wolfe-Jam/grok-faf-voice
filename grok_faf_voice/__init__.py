"""grok-faf-voice — the Voice Memory Layer (VML) for Grok Voice.

Reference implementation of VML — the persistence layer for what an
agent remembers across sessions, devices, and model switches. LiveKit
enabled.

Two layers, one FAF ecosystem:

- ``.faf``  — Foundational Context Layer (static, read once)
- ``.fafm`` — Voice Memory Layer / VML (mutating, persisted)

Three first-class objects + the Scratchpad primitive:

- ``FAFContext`` — static project DNA, read once per session.
  Loads ``.faf`` (``application/vnd.faf+yaml``, IANA-registered).
- ``FAFMemory``  — VML reference implementation. Reads/writes a soul
  on MCPaaS via the MCP protocol. ``.fafm``
  (``application/vnd.fafm+yaml``) is the planned IANA media type for
  the wire format.
- ``Scratchpad`` — in-session ephemeral key/value store, composed
  by ``FAFMemory``.

Plus a Context Bus for async pub/sub over voice-memory events
(``ContextBus`` + ``BusEvent`` + ``BusEventPayload``).

Stateless by default. Memory is a tool, not a baseline.
"""

from grok_faf_voice.agent import VoiceAgent, VoiceAgentConfigError
from grok_faf_voice.context import FAFContext
from grok_faf_voice.context_bus import BusEvent, BusEventPayload, ContextBus
from grok_faf_voice.custom_voices import CustomVoiceClient
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

__version__ = "0.2.1"
__all__ = [
    "BusEvent",
    "BusEventPayload",
    "ContextBus",
    "CustomVoiceClient",
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
    "VoiceAgent",
    "VoiceAgentConfigError",
    "VoiceSessionLedger",
    "enable_global_tool_bus",
]
