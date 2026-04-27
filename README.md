# grok-faf-voice

**Persistent memory for Grok Voice. LiveKit enabled.**

One import. Done.

Grok calls it **eternal soul state.**
What it means: memory that survives sessions, devices, model switches, even reinstalls.

```bash
pip install grok-faf-voice
```

```python
from grok_faf_voice import FAFContext, FAFMemory

faf = FAFContext("project.faf")              # static project DNA
mem = FAFMemory("grok", token="...")         # live voice memory layer
```

---

## What MCP couldn't do

MCP owns *context* — literally, in the protocol name. But voice agents
need more than static context. They need **memory** — live, audio-aware,
session-spanning state that survives interruptions, model switches, and
the gap between yesterday and today.

The Voice Memory Layer fills five gaps MCP doesn't reach:

| Gap | What it does | Built-in |
|---|---|---|
| **Scratchpad** | In-session ephemeral key/value with priority + smart-tag | ✅ |
| **Paralinguistic Tags** | Records HOW the user spoke (tone, emotional state, pace) | ✅ |
| **Smart Merge Engine** | Promotes scratchpad → permanent soul on session end, LLM-judged | ✅ |
| **Session Ledger** | Auditable record of every merge attempt, idempotent retries | ✅ |
| **Cross-session resumption** | Silently retries unfinished merges from prior sessions | ✅ |
| Real-time Context Bus | Sub-80ms mid-stream context mutation | _post-launch_ |

Five of six shipped. v0.0.8 is production-grade.

---

## Quickstart

```python
import os
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import xai

from grok_faf_voice import (
    LATENCY_BRIDGE_INSTRUCTIONS,
    FAFContext,
    FAFMemory,
    InMemoryVoiceSessionLedger,
)

faf = FAFContext("project.faf")
mem = FAFMemory(
    soul=os.environ.get("FAF_SOUL", "grok"),
    token=os.environ.get("MCPAAS_TOKEN"),
    ledger=InMemoryVoiceSessionLedger(),  # swap for a persistent backend in prod
)

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(
            voice="Ara",
            turn_detection={
                "type": "server_vad",
                "threshold": 0.85,
                "silence_duration_ms": 500,
                "prefix_padding_ms": 333,
            },
        ),
    )
    agent = Agent(
        instructions=f"{faf.system_prompt()}\n\n{LATENCY_BRIDGE_INSTRUCTIONS}",
        tools=mem.tools(session),  # etch + recall + paralinguistic + merge_now
    )

    # Awaitable shutdown callback — merge runs to completion on every
    # termination path (graceful close, disconnect, room destroy, drain).
    mem.attach_auto_merge(session, agent, strategy="grok-decides")

    await session.start(room=ctx.room, agent=agent)

    # Silently retry any incomplete merges from prior sessions. The user
    # only hears about it when severity is high (retry_count ≥ 2 or
    # many failed entries from a single attempt).
    await mem.on_session_start(session)


if __name__ == "__main__":
    agents.cli.run_app(server)
```

That's it. The agent now:

- Etches voice memories durably to `mcpaas.live` via MCP
- Records paralinguistic markers (how the user sounded, not just what they said)
- Promotes the scratchpad to permanent soul memory at session end via LLM judgment
- Resumes any unfinished work from the last session — invisibly, by default

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

## Architecture

Three first-class objects:

- **`FAFContext`** — static project DNA, read once per session.
  Loads `.faf` (`application/vnd.faf+yaml`, IANA-registered).
- **`FAFMemory`** — live voice memory layer. Reads/writes a soul on
  MCPaaS via the MCP protocol. Composes the scratchpad and ledger.
- **`Scratchpad`** — in-session ephemeral key/value store with
  priority + smart-tag metadata for the merge engine.

Plus the `VoiceSessionLedger` Protocol with two ready impls
(`NullVoiceSessionLedger` default, `InMemoryVoiceSessionLedger` for
tests + small demos). Persistent backends are downstream.

Stateless by default. Memory is a tool, not a baseline.

---

## Lineage

From the makers of [grok-faf-mcp](https://github.com/Wolfe-Jam/grok-faf-mcp), Grok's first ever MCP.

Companion to [FAF-Voice](https://github.com/Wolfe-Jam/FAF-Voice) — the multi-model frontend.

Built with care for the Grok Voice ecosystem. Not officially affiliated with xAI.

---

Do use FAF, fork it, share it, build with it, enjoy it.

**Don't copy FAF brand. Do your own.**
