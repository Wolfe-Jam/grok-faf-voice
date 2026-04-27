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

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import xai

from grok_faf_voice import FAFContext, FAFMemory

# Static project DNA — auto-detect file or MCPaaS slug
faf = FAFContext("project.faf")

# Live memory layer — bound to a soul, exposes etch + recall as tools
mem = FAFMemory(
    soul=os.environ.get("FAF_SOUL", "grok"),
    token=os.environ.get("MCPAAS_TOKEN"),
)

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(
            voice="Ara",
            model="grok-voice-think-fast-1.0",
        ),
        instructions=faf.system_prompt(),
        tools=mem.tools(),  # adds etch_memory + recall_memory
    )
    await session.start(room=ctx.room, agent=Agent())


if __name__ == "__main__":
    agents.cli.run_app(server)
