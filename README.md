# grok-faf-voice

**Persistent memory for Grok Voice. LiveKit enabled.**

One import. Done.

Grok calls it **eternal soul state.**
What it means: memory that survives sessions, devices, model switches, even reinstalls.

---

> Status: under active rebuild. v0.2.0 scaffolding is being replaced. Watch this space.

From the Makers of [grok-faf-mcp](https://github.com/Wolfe-Jam/grok-faf-mcp), Grok's first ever MCP.

Companion to [FAF-Voice](https://github.com/Wolfe-Jam/FAF-Voice) — the multi-model frontend.

Built with care for the Grok Voice ecosystem. Not officially affiliated with xAI.

---

## Tool Latency Budget (Realtime with Ara)

The realtime stream goes silent during tool execution — no automatic
filler audio. Tool authors should match the user-perceived band:

| Tool latency  | User perception     | Required action                                 |
|---------------|---------------------|-------------------------------------------------|
| < 400 ms      | Instant             | No bridge needed                                |
| 400–800 ms    | Light pause         | Short verbal bridge (Pattern A, via docstring)  |
| 800 ms–1.2 s  | Noticeable stall    | **Must** use verbal bridge                      |
| > 1.5 s       | Feels broken        | Explicit `session.say()` hold (Pattern B)       |

The four built-in tools follow these rules out of the box:

- `etch_memory`, `recall_memory`, `note_paralinguistic` — Pattern A
  (model speaks "Got it." / "Let me check..." before calling, per
  the `CRITICAL LATENCY RULE` in each tool's docstring).
- `merge_now` — Pattern B (the SDK emits an explicit verbal hold via
  `session.say()` before the multi-second merge runs, then a short
  confirmation after).

For custom tools, append `LATENCY_BRIDGE_INSTRUCTIONS` to your Agent
instructions to backstop the per-tool docstrings:

```python
from grok_faf_voice import LATENCY_BRIDGE_INSTRUCTIONS

agent = Agent(
    instructions=f"{your_prompt}\n\n{LATENCY_BRIDGE_INSTRUCTIONS}",
    tools=mem.tools(session),
)
```

---

Do use FAF, fork it, share it, build with it, enjoy it.

**Don't copy FAF brand. Do your own.**
