"""Hello, Grok — with memory AND etch.

Full Gate 2 surface: the agent loads project context AND can etch
voice memories to a persistent soul.

Run::

    pip install -e .
    export MCPAAS_TOKEN=your-token
    python examples/hello_grok_with_etch.py console

In the console session, try::

    "Etch this: my favourite colour is cyan"
    (close, restart)
    "What do you remember about me?"

The agent recalls the etched memory because it persisted to the
soul on MCPaaS — across sessions, devices, model switches.
"""

import os

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import xai

from grok_faf_voice import (
    LATENCY_BRIDGE_INSTRUCTIONS,
    FAFContext,
    FAFMemory,
    InMemoryVoiceSessionLedger,
)

# Load env vars from .env (XAI_API_KEY, LIVEKIT_*, MCPAAS_TOKEN)
load_dotenv()

# Static project DNA — auto-detect file or MCPaaS slug
faf = FAFContext("project.faf")

# Live memory layer — bound to a soul, exposes etch + recall as tools.
# Pass a ledger to record every Smart Merge attempt; the InMemory one
# lasts the process lifetime (swap for a persistent impl in production).
mem = FAFMemory(
    soul=os.environ.get("FAF_SOUL", "grok"),
    token=os.environ.get("MCPAAS_TOKEN"),
    ledger=InMemoryVoiceSessionLedger(),
)

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    # server_vad keeps interruption handling reliable on the realtime
    # path. Threshold/silence/prefix values are the SuperGrok-recommended
    # defaults for natural conversational turn-taking.
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

    # agent.add_shutdown_callback is awaited during graceful shutdown,
    # so the merge runs to completion (or explicit failure) on every
    # termination path — not fire-and-forget.
    mem.attach_auto_merge(session, agent, strategy="grok-decides")

    await session.start(room=ctx.room, agent=agent)


if __name__ == "__main__":
    agents.cli.run_app(server)
