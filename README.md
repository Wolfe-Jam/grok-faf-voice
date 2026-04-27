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
mem = FAFMemory("grok", token="...")         # live voice memory
```

---

## What it sounds like

```
$ python examples/hello_grok_with_etch.py console

[Agent]: Welcome back. Last time you mentioned shipping version 0.0.12,
         and you sounded excited about the test results. What's next?

[You]:   Etch this — the cross-session loop is verified.

[Agent]: Got it. Jotting that down.
```

The agent opens this session by referencing facts you etched in a
prior one. No magic prompt engineering — the SDK pre-loads the soul
into the model's context at session start.

---

## What's in the box

Voice agents need state that survives interruptions, model switches,
and the gap between yesterday and today. `grok-faf-voice` ships the
primitives:

| Primitive | What it does |
|---|---|
| **Scratchpad** | In-session ephemeral key/value with priority + smart-tag |
| **Paralinguistic Tags** | Records HOW the user spoke (tone, emotional state, pace) |
| **Smart Merge Engine** | Promotes scratchpad → permanent soul on session end, LLM-judged |
| **Session Ledger** | Auditable record of every merge attempt, idempotent retries |
| **Cross-session resumption** | Silently retries unfinished merges from prior sessions |
| **Soul → prompt bridge** | Pre-loads prior soul into the agent's instructions so it opens with continuity |
| **Context Bus** | Async pub/sub over voice-memory events — the right hooks at the right moments |

---

## Requirements

- Python 3.10+
- `XAI_API_KEY` — from [console.x.ai](https://console.x.ai)
- `LIVEKIT_URL` + `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET` — from [LiveKit Cloud](https://cloud.livekit.io)
- `MCPAAS_TOKEN` — for soul writes (reads work without a token on public souls)
- A `.faf` file describing your project (any text editor; minimal example below)

```yaml
# project.faf
faf_version: "3.0"
project:
  name: my-voice-agent
  goal: A voice assistant that remembers
human_context:
  who: developers
  what: voice agent with persistent memory
  why: continuity matters
```

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

    # Pull prior soul into the system prompt so the agent opens with
    # continuity — knowing what was etched in past sessions.
    prior_context = await mem.recall_for_prompt()

    agent = Agent(
        instructions=(
            f"{faf.system_prompt()}\n\n"
            f"{prior_context}\n\n"
            f"{LATENCY_BRIDGE_INSTRUCTIONS}"
        ),
        tools=mem.tools(session),  # etch + recall + paralinguistic + merge_now
    )

    await mem.start_bus()
    mem.attach_auto_merge(session, ctx, strategy="grok-decides")
    await session.start(room=ctx.room, agent=agent)
    await mem.on_session_start(session)


if __name__ == "__main__":
    agents.cli.run_app(server)
```

Run it:

```bash
python my_agent.py console
```

The agent now:

- Etches voice memories durably via MCP
- Records paralinguistic markers (HOW the user sounded, not just what they said)
- Promotes the scratchpad to permanent soul memory at session end
- Resumes any unfinished work from the last session — invisibly, by default
- Opens every session with prior context already loaded

---

## Architecture

Three first-class objects:

- **`FAFContext`** — static project DNA, read once per session.
  Loads `.faf` (`application/vnd.faf+yaml`, IANA-registered).
- **`FAFMemory`** — live voice memory. Reads/writes a soul on MCPaaS
  via the MCP protocol. Composes the scratchpad and ledger.
- **`Scratchpad`** — in-session ephemeral key/value store with
  priority + smart-tag metadata for the merge engine.

Plus the `VoiceSessionLedger` Protocol with two ready impls
(`NullVoiceSessionLedger` default, `InMemoryVoiceSessionLedger` for
tests + small demos). Persistent backends are downstream.

Stateless by default. Memory is a tool, not a baseline.

---

## Context Bus — events for advanced agents

Voice memory is most useful when other code can react to it. The
Context Bus gives developers precise, semantic control with full
async power and backpressure: subscribe to events like
`tool.about_to_run`, `paralinguistic.detected`, `merge.starting`,
`session.resumed`, and 9 more.

```python
from grok_faf_voice import BusEvent, BusEventPayload

@mem.bus.on(BusEvent.PARALINGUISTIC_DETECTED)
async def on_paralinguistic(payload: BusEventPayload) -> None:
    # Fires every time the agent records HOW the user spoke
    print(payload.payload)  # {"marker_type": "tone", "value": "frustrated", ...}

@mem.bus.on(BusEvent.MERGE_STARTING)
async def on_merge(payload: BusEventPayload) -> None:
    # Fires before a Smart Merge runs — auto-merge or explicit merge_now
    print(f"merging: {payload.payload['reason']}")

await mem.start_bus()
```

Sync handlers work too — pass them to `mem.bus.on_sync(event, fn)`.
Failing handlers are logged but never block the rest of the bus.

To give the Bus full visibility over **every** tool the agent runs —
FAFMemory's own four AND any user-defined tools — call
`enable_global_tool_bus(mem, agent)` once after Agent construction.
Idempotent; built-in tools are tagged so they aren't double-wrapped.

```python
from grok_faf_voice import enable_global_tool_bus

agent = Agent(instructions=..., tools=mem.tools(session) + user_tools)
enable_global_tool_bus(mem, agent)
# Now every tool fires bus.tool.about_to_run + bus.tool.completed
```

The complete event vocabulary is exported as `BusEvent`:

```
scratchpad.updated      scratchpad.dirty       soul.updated
memory.snapshot         paralinguistic.detected
tool.about_to_run       tool.completed
merge.pending           merge.starting         merge.completed
session.resumed         context.invalidated    audio.cue
```

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
  (model speaks "Got it." / "Let me check..." before calling).
- `merge_now` — Pattern B (the SDK emits an explicit verbal hold via
  `session.say()` before the multi-second merge runs, then a short
  confirmation after).

For custom tools, append `LATENCY_BRIDGE_INSTRUCTIONS` to your Agent
instructions:

```python
from grok_faf_voice import LATENCY_BRIDGE_INSTRUCTIONS

agent = Agent(
    instructions=f"{your_prompt}\n\n{LATENCY_BRIDGE_INSTRUCTIONS}",
    tools=mem.tools(session),
)
```

---

## Lineage

From the makers of [grok-faf-mcp](https://github.com/Wolfe-Jam/grok-faf-mcp), Grok's first ever MCP.

Companion to [FAF-Voice](https://github.com/Wolfe-Jam/FAF-Voice) — the multi-model frontend.

Built with care for the Grok Voice ecosystem. Not officially affiliated with xAI.

---

## License

MIT.

Do use FAF, fork it, share it, build with it, enjoy it.

**Don't copy FAF brand. Do your own.**
