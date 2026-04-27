"""grok-faf-voice — Persistent memory for Grok Voice. LiveKit enabled.

Two first-class siblings:

- ``FAFContext`` — static project DNA, read once per session.
  Loads `.faf` (`application/vnd.faf+yaml`).
- ``FAFMemory``  — live voice memory layer.
  Loads `.fafm` (`application/vnd.fafm+yaml` planned).

Stateless by default. Memory is a tool, not a baseline.
"""

from grok_faf_voice.context import FAFContext
from grok_faf_voice.memory import FAFMemory

__version__ = "0.0.1"
__all__ = ["FAFContext", "FAFMemory"]
